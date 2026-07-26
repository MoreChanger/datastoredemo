package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
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
	trashPath := getEnv("TRASH_PATH", "../data/trash")
	graceHours := getEnvInt("GRACE_HOURS", 24)
	serverPort := getEnv("SERVER_PORT", "8081")

	// Initialize storage
	store, err := storage.NewPostgresStorage(dbHost, dbPort, dbUser, dbPass, dbName)
	if err != nil {
		log.Fatalf("Failed to connect to database: %v", err)
	}
	defer store.Close()

	// Initialize cleaner
	cleaner := storage.NewCleaner(store, blobPath, trashPath, graceHours)

	// Setup HTTP server
	router := gin.Default()
	router.GET("/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "healthy"})
	})

	// Cleaner API
	handler := api.NewCleanerHandler(cleaner)
	router.GET("/api/v1/cleanup/report", handler.GetCleanupReport)
	router.POST("/api/v1/cleanup/dry-run", handler.DryRunCleanup)
	router.POST("/api/v1/cleanup/execute", handler.ExecuteCleanup)
	router.POST("/api/v1/cleanup/empty-trash", handler.EmptyTrash)
	router.GET("/api/v1/blobs/ref-counts", handler.GetRefCounts)

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

	log.Printf("Cleaner service started on port %s", serverPort)

	// Wait for interrupt signal
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit

	log.Println("Shutting down cleaner service...")

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if err := srv.Shutdown(ctx); err != nil {
		log.Fatal("Server forced to shutdown:", err)
	}

	log.Println("Cleaner service stopped")
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
		_ = intValue // avoid unused variable error for now
	}
	return defaultValue
}
