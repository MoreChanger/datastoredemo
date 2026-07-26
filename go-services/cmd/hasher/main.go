package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/loongclouds/datastoredemo/go-services/internal/api"
	"github.com/loongclouds/datastoredemo/go-services/internal/storage"
)

func main() {
	// Configuration from environment
	dbHost := getEnv("POSTGRES_HOST", "localhost")
	dbPort := getEnv("POSTGRES_PORT", "5432")
	dbUser := getEnv("POSTGRES_USER", "loongclouds")
	dbPass := getEnv("POSTGRES_PASSWORD", "loongclouds_dev_password")
	dbName := getEnv("POSTGRES_DATABASE", "storage")
	blobPath := getEnv("BLOB_STORAGE_PATH", "../data/storage")
	serverPort := getEnv("SERVER_PORT", "8082")
	workerCount := getEnvInt("WORKER_COUNT", 4)

	// Initialize storage
	store, err := storage.NewPostgresStorage(dbHost, dbPort, dbUser, dbPass, dbName)
	if err != nil {
		log.Fatalf("Failed to connect to database: %v", err)
	}
	defer store.Close()

	// Initialize hasher
	hasher := storage.NewBatchHasher(blobPath, workerCount)

	// Setup HTTP server
	router := gin.Default()
	router.GET("/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "healthy"})
	})

	// Hasher API
	handler := api.NewHasherHandler(hasher, store)
	router.POST("/api/v1/hash/file", handler.HashFile)
	router.POST("/api/v1/hash/batch", handler.HashBatch)
	router.GET("/api/v1/hash/stats", handler.GetStats)

	// Start server
	srv := &http.Server{
		Addr:    ":" + serverPort,
		Handler: router,
	}

	// Graceful shutdown
	go func() {
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("Failed to start server: %v", err)
		}
	}()

	log.Printf("Hasher service started on port %s with %d workers", serverPort, workerCount)

	// Wait for interrupt signal
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit

	log.Println("Shutting down hasher service...")

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if err := srv.Shutdown(ctx); err != nil {
		log.Fatal("Server forced to shutdown:", err)
	}

	log.Println("Hasher service stopped")
}

func getEnv(key, defaultValue string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return defaultValue
}

func getEnvInt(key string, defaultValue int) int {
	if value := os.Getenv(key); value != "" {
		var intValue int
		if _, err := os.Stat(key); err == nil {
			return defaultValue
		}
		_ = intValue
	}
	return defaultValue
}

// WaitGroup for batch operations
type WaitGroup struct {
	sync.WaitGroup
}
