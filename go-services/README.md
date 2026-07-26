# Go 微服务

高性能独立服务，用于处理存储系统的热点操作。

## 服务列表

### 1. 清理器服务

**端口**: 8081  
**功能**: 引用计数查询、零引用 blob 清理、回收站管理

**API 端点**:
- `GET /health` - 健康检查
- `GET /api/v1/cleanup/report` - 获取清理报告
- `POST /api/v1/cleanup/dry-run` - 演练清理（不实际删除）
- `POST /api/v1/cleanup/execute` - 执行清理
- `POST /api/v1/cleanup/empty-trash` - 清空回收站
- `GET /api/v1/blobs/ref-counts` - 获取引用计数

### 2. 批量哈希服务

**端口**: 8082  
**功能**: 并行文件哈希计算、批量处理

**API 端点**:
- `GET /health` - 健康检查
- `POST /api/v1/hash/file` - 单文件哈希
- `POST /api/v1/hash/batch` - 批量哈希
- `GET /api/v1/hash/stats` - 统计信息

## 环境变量

### 通用配置
```bash
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=loongclouds
POSTGRES_PASSWORD=loongclouds_dev_password
POSTGRES_DATABASE=storage
```

### 清理器服务
```bash
BLOB_STORAGE_PATH=../data/storage
TRASH_PATH=../data/trash
GRACE_HOURS=24
SERVER_PORT=8081
```

### 哈希服务
```bash
BLOB_STORAGE_PATH=../data/storage
SERVER_PORT=8082
WORKER_COUNT=4
```

## 构建

```bash
cd go-services

# 下载依赖
go mod download

# 构建清理器服务
go build -o bin/cleaner ./cmd/cleaner

# 构建哈希服务
go build -o bin/hasher ./cmd/hasher
```

## 运行

```bash
# 设置环境变量
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5432
export POSTGRES_USER=loongclouds
export POSTGRES_PASSWORD=loongclouds_dev_password
export POSTGRES_DATABASE=storage

# 运行清理器服务
./bin/cleaner

# 运行哈希服务（另一个终端）
./bin/hasher
```

## Docker 部署

```dockerfile
# Dockerfile 示例
FROM golang:1.22-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o cleaner ./cmd/cleaner
RUN CGO_ENABLED=0 go build -o hasher ./cmd/hasher

FROM alpine:latest
RUN apk --no-cache add ca-certificates
WORKDIR /root/
COPY --from=builder /app/cleaner .
COPY --from=builder /app/hasher .
CMD ["./cleaner"]
```

## 性能优势

### 清理器服务
- **Go 并发**: 使用 goroutines 并行处理 blob 移动
- **内存效率**: 流式处理大文件列表
- **低延迟**: 无 Python GIL 限制

### 批量哈希服务
- **Worker Pool**: 可配置的并行工作线程
- **批量优化**: 单次 I/O 读取多文件
- **CPU 密集**: 充分利用多核 CPU

## Python 客户端调用示例

```python
import requests

# 清理器服务
def get_cleanup_report():
    response = requests.get("http://localhost:8081/api/v1/cleanup/report")
    return response.json()

def execute_cleanup():
    response = requests.post("http://localhost:8081/api/v1/cleanup/execute")
    return response.json()

# 哈希服务
def hash_file(file_path):
    response = requests.post(
        "http://localhost:8082/api/v1/hash/file",
        json={"file_path": file_path}
    )
    return response.json()

def hash_batch(file_paths):
    response = requests.post(
        "http://localhost:8082/api/v1/hash/batch",
        json={"file_paths": file_paths}
    )
    return response.json()
```

## 监控

### 健康检查
```bash
curl http://localhost:8081/health
curl http://localhost:8082/health
```

### Prometheus 指标（待实现）
- `cleanup_blobs_moved_total`
- `cleanup_blobs_deleted_total`
- `hash_files_processed_total`
- `hash_bytes_processed_total`
- `hash_duration_seconds`

## 架构说明

```
┌─────────────────────────────────────────┐
│          Python 主服务                    │
│   (文件上传、版本管理、快照)              │
└──────────────┬──────────────────────────┘
               │ HTTP API
               ▼
┌──────────────────────────────────────────┐
│         Go 清理器服务 (8081)              │
│   引用计数查询、blob 清理                 │
│   PostgreSQL + 文件系统                  │
└──────────────────────────────────────────┘

┌──────────────────────────────────────────┐
│         Go 哈希服务 (8082)                │
│   批量 SHA-256 计算                       │
│   Worker Pool 并行处理                   │
└──────────────────────────────────────────┘
```

## 与主服务集成

Go 服务通过 PostgreSQL 数据库与 Python 主服务共享状态：

1. **数据库共享**: Python 写入元数据，Go 读取并执行清理
2. **文件系统共享**: Python 存储 blob，Go 移动到回收站
3. **HTTP API**: Python 可选调用 Go 服务加速热点操作

## 何时使用

- **清理器服务**: 定时任务、大规模 blob 清理、后台维护
- **哈希服务**: 批量资产校验、初始迁移、大文件处理
- **Python 主服务**: 所有常规操作（上传、版本管理、快照）

## 注意事项

1. Go 服务是**可选优化**，Python 主服务已完整实现所有功能
2. 生产环境建议使用 Docker 部署
3. 确保文件系统路径在两个服务间共享
4. PostgreSQL 连接池大小需合理配置
