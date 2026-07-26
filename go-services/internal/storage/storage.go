package storage

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// PostgresStorage handles database connections
type PostgresStorage struct {
	pool *pgxpool.Pool
}

// NewPostgresStorage creates a new PostgreSQL storage connection
func NewPostgresStorage(host, port, user, password, database string) (*PostgresStorage, error) {
	connStr := fmt.Sprintf("host=%s port=%s user=%s password=%s dbname=%s sslmode=disable",
		host, port, user, password, database)

	pool, err := pgxpool.New(context.Background(), connStr)
	if err != nil {
		return nil, fmt.Errorf("failed to create connection pool: %w", err)
	}

	// Test connection
	if err := pool.Ping(context.Background()); err != nil {
		return nil, fmt.Errorf("failed to ping database: %w", err)
	}

	return &PostgresStorage{pool: pool}, nil
}

// Close closes the connection pool
func (s *PostgresStorage) Close() {
	if s.pool != nil {
		s.pool.Close()
	}
}

// GetPool returns the connection pool
func (s *PostgresStorage) GetPool() *pgxpool.Pool {
	return s.pool
}

// Cleaner handles blob cleanup operations
type Cleaner struct {
	store      *PostgresStorage
	blobPath   string
	trashPath  string
	graceHours int
}

// NewCleaner creates a new cleaner instance
func NewCleaner(store *PostgresStorage, blobPath, trashPath string, graceHours int) *Cleaner {
	return &Cleaner{
		store:      store,
		blobPath:   blobPath,
		trashPath:  trashPath,
		graceHours: graceHours,
	}
}

// RefCount represents blob reference count information
type RefCount struct {
	Hash      string `json:"hash"`
	UserRefs  int    `json:"user_refs"`
	JobRefs   int    `json:"job_refs"`
	TotalRefs int    `json:"total_refs"`
}

// CleanupReport represents cleanup report information
type CleanupReport struct {
	Blobs struct {
		TotalCount   int `json:"total_count"`
		ActiveCount  int `json:"active_count"`
		ZeroRefCount int `json:"zero_ref_count"`
		TotalSizeMB  int `json:"total_size_mb"`
	} `json:"blobs"`
	Trash struct {
		FileCount      int `json:"file_count"`
		TotalSizeMB    int `json:"total_size_mb"`
		OldestAgeHours int `json:"oldest_age_hours"`
	} `json:"trash"`
}

// GetRefCounts returns reference counts for all blobs
func (c *Cleaner) GetRefCounts() ([]RefCount, error) {
	ctx := context.Background()
	rows, err := c.store.pool.Query(ctx, `
		SELECT hash, user_dir_refs, manifest_refs, total_refs
		FROM blob_ref_counts
		ORDER BY total_refs DESC
	`)
	if err != nil {
		return nil, fmt.Errorf("failed to query ref counts: %w", err)
	}
	defer rows.Close()

	var counts []RefCount
	for rows.Next() {
		var rc RefCount
		if err := rows.Scan(&rc.Hash, &rc.UserRefs, &rc.JobRefs, &rc.TotalRefs); err != nil {
			return nil, fmt.Errorf("failed to scan row: %w", err)
		}
		counts = append(counts, rc)
	}

	return counts, nil
}

// GetZeroRefHashes returns hashes with zero references
func (c *Cleaner) GetZeroRefHashes() ([]string, error) {
	ctx := context.Background()
	rows, err := c.store.pool.Query(ctx, `
		SELECT hash FROM blob_ref_counts WHERE total_refs = 0
	`)
	if err != nil {
		return nil, fmt.Errorf("failed to query zero ref hashes: %w", err)
	}
	defer rows.Close()

	var hashes []string
	for rows.Next() {
		var hash string
		if err := rows.Scan(&hash); err != nil {
			return nil, fmt.Errorf("failed to scan hash: %w", err)
		}
		hashes = append(hashes, hash)
	}

	return hashes, nil
}

// GetCleanupReport returns cleanup report
func (c *Cleaner) GetCleanupReport() (*CleanupReport, error) {
	report := &CleanupReport{}

	// Get blob statistics
	ctx := context.Background()
	err := c.store.pool.QueryRow(ctx, `
		SELECT 
			COUNT(*) as total_count,
			COUNT(*) FILTER (WHERE total_refs > 0) as active_count,
			COUNT(*) FILTER (WHERE total_refs = 0) as zero_ref_count
		FROM blob_ref_counts
	`).Scan(&report.Blobs.TotalCount, &report.Blobs.ActiveCount, &report.Blobs.ZeroRefCount)
	if err != nil {
		return nil, fmt.Errorf("failed to get blob stats: %w", err)
	}

	// Calculate total size from blob storage
	report.Blobs.TotalSizeMB = c.calculateBlobSizeMB()

	// Get trash statistics
	report.Trash.FileCount, report.Trash.TotalSizeMB, report.Trash.OldestAgeHours = c.getTrashStats()

	return report, nil
}

// DryRunCleanup simulates cleanup without actually deleting
func (c *Cleaner) DryRunCleanup() (map[string]int, error) {
	hashes, err := c.GetZeroRefHashes()
	if err != nil {
		return nil, err
	}

	return map[string]int{
		"would_move": len(hashes),
	}, nil
}

// ExecuteCleanup moves zero-reference blobs to trash
func (c *Cleaner) ExecuteCleanup() (map[string]int, error) {
	hashes, err := c.GetZeroRefHashes()
	if err != nil {
		return nil, err
	}

	stats := map[string]int{
		"moved":  0,
		"failed": 0,
	}

	for _, hash := range hashes {
		if c.moveToTrash(hash) {
			stats["moved"]++
		} else {
			stats["failed"]++
		}
	}

	return stats, nil
}

// EmptyTrash deletes old files from trash
func (c *Cleaner) EmptyTrash(force bool) (map[string]int, error) {
	stats := map[string]int{
		"deleted": 0,
		"kept":    0,
		"errors":  0,
	}

	// Walk trash directory
	err := filepath.Walk(c.trashPath, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return nil
		}

		if info.IsDir() {
			return nil
		}

		fileAge := time.Since(info.ModTime())
		graceDuration := time.Duration(c.graceHours) * time.Hour

		if force || fileAge > graceDuration {
			if err := os.Remove(path); err == nil {
				stats["deleted"]++
			} else {
				stats["errors"]++
			}
		} else {
			stats["kept"]++
		}

		return nil
	})

	return stats, err
}

func (c *Cleaner) moveToTrash(hash string) bool {
	blobPath := filepath.Join(c.blobPath, hash[:2], hash[2:4], hash)
	trashPath := filepath.Join(c.trashPath, hash[:2], hash[2:4], hash)

	// Check if blob exists
	if _, err := os.Stat(blobPath); os.IsNotExist(err) {
		return false
	}

	// Create trash directory
	if err := os.MkdirAll(filepath.Dir(trashPath), 0755); err != nil {
		return false
	}

	// Move file
	if err := os.Rename(blobPath, trashPath); err != nil {
		return false
	}

	return true
}

func (c *Cleaner) calculateBlobSizeMB() int {
	var totalSize int64
	filepath.Walk(c.blobPath, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return nil
		}
		if !info.IsDir() {
			totalSize += info.Size()
		}
		return nil
	})
	return int(totalSize / (1024 * 1024))
}

func (c *Cleaner) getTrashStats() (fileCount, totalSizeMB, oldestAgeHours int) {
	var oldestTime time.Time

	filepath.Walk(c.trashPath, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return nil
		}
		if !info.IsDir() {
			fileCount++
			totalSizeMB += int(info.Size() / 1024 / 1024)
			if oldestTime.IsZero() || info.ModTime().Before(oldestTime) {
				oldestTime = info.ModTime()
			}
		}
		return nil
	})

	if !oldestTime.IsZero() {
		oldestAgeHours = int(time.Since(oldestTime).Hours())
	}

	return
}

// BatchHasher handles batch hash computation
type BatchHasher struct {
	blobPath    string
	workerCount int
	stats       HasherStats
	mu          sync.Mutex
}

// HasherStats tracks hasher statistics
type HasherStats struct {
	FilesProcessed int64 `json:"files_processed"`
	TotalBytes     int64 `json:"total_bytes"`
	AvgTimeMS      int64 `json:"avg_time_ms"`
}

// NewBatchHasher creates a new batch hasher
func NewBatchHasher(blobPath string, workerCount int) *BatchHasher {
	return &BatchHasher{
		blobPath:    blobPath,
		workerCount: workerCount,
	}
}

// HashRequest represents a hash computation request
type HashRequest struct {
	FilePath string `json:"file_path"`
}

// HashResponse represents a hash computation response
type HashResponse struct {
	FilePath string `json:"file_path"`
	Hash     string `json:"hash"`
	Size     int64  `json:"size"`
	Error    string `json:"error,omitempty"`
}

// ComputeHash computes SHA-256 hash of a file
// Note: This is a placeholder - actual implementation would use crypto/sha256
func (h *BatchHasher) ComputeHash(filePath string) *HashResponse {
	start := time.Now()
	resp := &HashResponse{FilePath: filePath}

	// Get file info
	info, err := os.Stat(filePath)
	if err != nil {
		resp.Error = err.Error()
		return resp
	}

	resp.Size = info.Size()

	// Placeholder hash computation
	// In production, this would read and hash the file
	resp.Hash = fmt.Sprintf("%x", time.Now().UnixNano())

	// Update stats
	h.mu.Lock()
	h.stats.FilesProcessed++
	h.stats.TotalBytes += resp.Size
	elapsed := time.Since(start).Milliseconds()
	if h.stats.FilesProcessed > 0 {
		h.stats.AvgTimeMS = (h.stats.AvgTimeMS*(h.stats.FilesProcessed-1) + elapsed) / h.stats.FilesProcessed
	}
	h.mu.Unlock()

	return resp
}

// GetStats returns hasher statistics
func (h *BatchHasher) GetStats() HasherStats {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.stats
}
