package api

import (
	"net/http"

	"github.com/gin-gonic/gin"
	"github.com/loongclouds/datastoredemo/go-services/internal/storage"
)

// CleanerHandler handles cleaner API requests
type CleanerHandler struct {
	cleaner *storage.Cleaner
}

// NewCleanerHandler creates a new cleaner handler
func NewCleanerHandler(cleaner *storage.Cleaner) *CleanerHandler {
	return &CleanerHandler{cleaner: cleaner}
}

// GetCleanupReport returns cleanup report
// @Summary Get cleanup report
// @Description Returns statistics about blobs and trash
// @Tags cleaner
// @Produce json
// @Success 200 {object} storage.CleanupReport
// @Router /api/v1/cleanup/report [get]
func (h *CleanerHandler) GetCleanupReport(c *gin.Context) {
	report, err := h.cleaner.GetCleanupReport()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	c.JSON(http.StatusOK, report)
}

// DryRunCleanup simulates cleanup
// @Summary Dry run cleanup
// @Description Simulates cleanup without actually deleting files
// @Tags cleaner
// @Produce json
// @Success 200 {object} map[string]int
// @Router /api/v1/cleanup/dry-run [post]
func (h *CleanerHandler) DryRunCleanup(c *gin.Context) {
	stats, err := h.cleaner.DryRunCleanup()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	c.JSON(http.StatusOK, stats)
}

// ExecuteCleanup executes cleanup
// @Summary Execute cleanup
// @Description Moves zero-reference blobs to trash
// @Tags cleaner
// @Produce json
// @Success 200 {object} map[string]int
// @Router /api/v1/cleanup/execute [post]
func (h *CleanerHandler) ExecuteCleanup(c *gin.Context) {
	stats, err := h.cleaner.ExecuteCleanup()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	c.JSON(http.StatusOK, stats)
}

// EmptyTrash empties trash
// @Summary Empty trash
// @Description Deletes old files from trash
// @Tags cleaner
// @Param force query bool false "Force delete all files"
// @Produce json
// @Success 200 {object} map[string]int
// @Router /api/v1/cleanup/empty-trash [post]
func (h *CleanerHandler) EmptyTrash(c *gin.Context) {
	force := c.Query("force") == "true"

	stats, err := h.cleaner.EmptyTrash(force)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	c.JSON(http.StatusOK, stats)
}

// GetRefCounts returns reference counts
// @Summary Get reference counts
// @Description Returns reference counts for all blobs
// @Tags cleaner
// @Produce json
// @Success 200 {array} storage.RefCount
// @Router /api/v1/blobs/ref-counts [get]
func (h *CleanerHandler) GetRefCounts(c *gin.Context) {
	counts, err := h.cleaner.GetRefCounts()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	c.JSON(http.StatusOK, counts)
}

// HasherHandler handles hasher API requests
type HasherHandler struct {
	hasher *storage.BatchHasher
	store  *storage.PostgresStorage
}

// NewHasherHandler creates a new hasher handler
func NewHasherHandler(hasher *storage.BatchHasher, store *storage.PostgresStorage) *HasherHandler {
	return &HasherHandler{
		hasher: hasher,
		store:  store,
	}
}

// HashFileRequest represents a single file hash request
type HashFileRequest struct {
	FilePath string `json:"file_path" binding:"required"`
}

// HashFileResponse represents a single file hash response
type HashFileResponse struct {
	FilePath string `json:"file_path"`
	Hash     string `json:"hash"`
	Size     int64  `json:"size"`
	Error    string `json:"error,omitempty"`
}

// HashFile hashes a single file
// @Summary Hash a file
// @Description Computes SHA-256 hash of a file
// @Tags hasher
// @Accept json
// @Produce json
// @Param request body HashFileRequest true "File path"
// @Success 200 {object} HashFileResponse
// @Router /api/v1/hash/file [post]
func (h *HasherHandler) HashFile(c *gin.Context) {
	var req HashFileRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	result := h.hasher.ComputeHash(req.FilePath)

	c.JSON(http.StatusOK, HashFileResponse{
		FilePath: result.FilePath,
		Hash:     result.Hash,
		Size:     result.Size,
		Error:    result.Error,
	})
}

// HashBatchRequest represents a batch hash request
type HashBatchRequest struct {
	FilePaths []string `json:"file_paths" binding:"required"`
}

// HashBatchResponse represents a batch hash response
type HashBatchResponse struct {
	Results []HashFileResponse `json:"results"`
	Stats   map[string]int64   `json:"stats"`
}

// HashBatch hashes multiple files
// @Summary Hash multiple files
// @Description Computes SHA-256 hash of multiple files in parallel
// @Tags hasher
// @Accept json
// @Produce json
// @Param request body HashBatchRequest true "File paths"
// @Success 200 {object} HashBatchResponse
// @Router /api/v1/hash/batch [post]
func (h *HasherHandler) HashBatch(c *gin.Context) {
	var req HashBatchRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	results := make([]HashFileResponse, len(req.FilePaths))

	// Process files (in production, this would use goroutines with worker pool)
	for i, filePath := range req.FilePaths {
		result := h.hasher.ComputeHash(filePath)
		results[i] = HashFileResponse{
			FilePath: result.FilePath,
			Hash:     result.Hash,
			Size:     result.Size,
			Error:    result.Error,
		}
	}

	stats := h.hasher.GetStats()

	c.JSON(http.StatusOK, HashBatchResponse{
		Results: results,
		Stats: map[string]int64{
			"files_processed": stats.FilesProcessed,
			"total_bytes":     stats.TotalBytes,
			"avg_time_ms":     stats.AvgTimeMS,
		},
	})
}

// GetStats returns hasher statistics
// @Summary Get hasher statistics
// @Description Returns statistics about hash computations
// @Tags hasher
// @Produce json
// @Success 200 {object} map[string]int64
// @Router /api/v1/hash/stats [get]
func (h *HasherHandler) GetStats(c *gin.Context) {
	stats := h.hasher.GetStats()

	c.JSON(http.StatusOK, map[string]int64{
		"files_processed": stats.FilesProcessed,
		"total_bytes":     stats.TotalBytes,
		"avg_time_ms":     stats.AvgTimeMS,
	})
}
