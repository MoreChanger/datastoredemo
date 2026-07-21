# LoongClouds 存储模块详细设计 v1.0

## 1. 模块概述

存储模块是 LoongClouds 的核心基础设施，负责资产文件的内容寻址存储、版本管理、快照锁定和引用计数。

### 核心职责

| 职责 | 说明 |
|-----|-----|
| 内容寻址存储 | SHA-256 哈希作为文件唯一标识，全局去重 |
| 版本管理 | 自动记录文件版本历史 |
| 快照锁定 | 任务提交时锁定资产版本，确保可复现 |
| 引用计数 | 跟踪 blob 引用，自动清理无引用文件 |
| 双通道访问 | 本地 SMB + 云端 S3 访问方式 |

---

## 2. 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                      Storage Module                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │                    FileManager                        │  │
│  │    (文件上传、版本管理、路径解析)                       │  │
│  └───────────────────────────────────────────────────────┘  │
│                            │                                │
│                            ▼                                │
│  ┌───────────────────────────────────────────────────────┐  │
│  │                  ManifestManager                      │  │
│  │    (任务快照创建、版本锁定)                             │  │
│  └───────────────────────────────────────────────────────┘  │
│                            │                                │
│                            ▼                                │
│  ┌───────────────────────┐  ┌───────────────────────────┐  │
│  │     BlobStorage       │  │      MetadataStore        │  │
│  │  (Local / S3)         │  │     (PostgreSQL)          │  │
│  └───────────────────────┘  └───────────────────────────┘  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 存储层设计

### 3.1 目录结构

```
/data/loongclouds/
├── blobs/                          # 内容寻址存储
│   ├── a1/
│   │   └── b2/
│   │       └── a1b2c3d4...        # blob 文件（按哈希分片）
│   ├── .uploading/                 # 上传临时目录
│   └── ...
│
├── users/{username}/               # 用户工作目录
│   ├── D/                          # 盘符目录
│   │   └── projects/
│   │       └── shot_01/
│   │           ├── textures/
│   │           │   └── wood.png → /blobs/.../a1b2...
│   │           └── scenes/
│   │               └── main.ma → /blobs/.../e5f6...
│   └── ...
│
├── jobs/{job_id}/                  # 任务快照目录
│   ├── manifest.json               # 快照清单
│   ├── D/                          # 盘符目录（软链接锁定版本）
│   └── output → /outputs/{job_id}  # 输出目录链接
│
├── outputs/{job_id}/               # 渲染输出目录
│   ├── frame_0001.exr
│   └── ...
│
└── trash/                          # 回收站
    └── {hash_prefix}/
        └── {hash}
```

### 3.2 Blob 存储策略

**内容寻址规则**：
- 文件名 = SHA-256 哈希值（64 字符十六进制）
- 路径分片：`/blobs/{hash[0:2]}/{hash[2:4]}/{hash}`
- 优点：65536 个子目录，避免单目录文件过多

**去重机制**：
```go
func (s *LocalBlobStorage) Store(localPath, hash string) (string, error) {
    blobPath := getBlobPath(hash, s.basePath)
    
    // 检查是否已存在
    if exists, _ := s.Exists(hash); exists {
        return blobPath, nil // 去重，直接返回
    }
    
    // 写入临时文件
    tempPath := blobPath + ".uploading"
    if err := copyFile(localPath, tempPath); err != nil {
        return "", err
    }
    
    // 校验完整性
    actualHash, _ := hashFile(tempPath)
    if actualHash != hash {
        os.Remove(tempPath)
        return "", errors.New("hash mismatch")
    }
    
    // 原子重命名
    if err := os.Rename(tempPath, blobPath); err != nil {
        return "", err
    }
    
    return blobPath, nil
}
```

---

## 4. 数据流设计

### 4.1 文件上传流程

```
┌──────────────────────────────────────────────────────────────────┐
│                        文件上传流程                               │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. 客户端上传文件                                                │
│     │                                                            │
│     ▼                                                            │
│  2. 服务端接收文件到临时目录                                       │
│     │                                                            │
│     ▼                                                            │
│  3. 检查本地缓存 (mtime + size)                                   │
│     │                                                            │
│     ├── 命中 → 复用缓存哈希，跳过计算                              │
│     │                                                            │
│     └── 未命中 → 计算 SHA-256                                     │
│     │                                                            │
│     ▼                                                            │
│  4. 写入 Blob 存储（去重检查）                                     │
│     │                                                            │
│     ├── 已存在 → 直接返回                                          │
│     │                                                            │
│     └── 不存在 → 写入临时文件 → 校验 → 原子重命名                    │
│     │                                                            │
│     ▼                                                            │
│  5. 数据库事务                                                    │
│     │                                                            │
│     ├── 创建/更新 files 记录                                       │
│     ├── 创建新版本记录 (version_num++)                             │
│     └── 提交事务                                                  │
│     │                                                            │
│     ▼                                                            │
│  6. 更新用户目录软链接                                             │
│     │                                                            │
│     ├── 创建临时链接 (.tmp)                                        │
│     └── 原子重命名覆盖                                             │
│     │                                                            │
│     ▼                                                            │
│  7. 返回上传结果                                                   │
│     └── {path, hash, version, size, deduplicated}                 │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### 4.2 任务提交流程

```
┌──────────────────────────────────────────────────────────────────┐
│                        任务提交流程                               │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. 用户提交渲染任务                                              │
│     │                                                            │
│     ▼                                                            │
│  2. 解析场景依赖（调用 Python 脚本）                               │
│     │                                                            │
│     ├── 贴图文件                                                  │
│     ├── 引用文件                                                  │
│     ├── Proxy 文件                                                │
│     └── 其他依赖                                                  │
│     │                                                            │
│     ▼                                                            │
│  3. 生成文件清单                                                  │
│     │                                                            │
│     ├── 计算每个文件的哈希                                         │
│     ├── 查询数据库获取版本号                                       │
│     └── 构建依赖列表                                              │
│     │                                                            │
│     ▼                                                            │
│  4. 创建任务快照                                                  │
│     │                                                            │
│     ├── 生成 job_id                                               │
│     ├── 创建 /jobs/{job_id}/ 目录                                 │
│     ├── 创建软链接指向 blob                                        │
│     ├── 写入 manifest.json                                        │
│     └── 设置目录为只读 (immutable)                                 │
│     │                                                            │
│     ▼                                                            │
│  5. 数据库事务                                                    │
│     │                                                            │
│     ├── 创建 manifests 记录                                        │
│     ├── 创建 manifest_entries 记录                                 │
│     └── 提交事务                                                  │
│     │                                                            │
│     ▼                                                            │
│  6. 加入渲染队列                                                  │
│     │                                                            │
│     ▼                                                            │
│  7. 返回 job_id                                                  │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### 4.3 引用计数流程

```
┌──────────────────────────────────────────────────────────────────┐
│                      引用计数与清理流程                            │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  blob_ref_counts 视图                                            │
│  ───────────────────                                             │
│  SELECT hash, SUM(ref_count) AS ref_count                        │
│  FROM (                                                          │
│      -- 活跃 Job 引用                                             │
│      SELECT hash, COUNT(*) FROM manifest_entries                  │
│      JOIN manifests ON ... WHERE status NOT IN (archived,deleted)│
│                                                                  │
│      UNION ALL                                                   │
│                                                                  │
│      -- 用户目录最新版本引用                                        │
│      SELECT DISTINCT ON (file_id) hash FROM file_versions         │
│      JOIN files ON ... WHERE status != 'deleted'                  │
│  ) GROUP BY hash                                                 │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  定时清理任务 (cleanup_zero_ref_blobs)                            │
│  ────────────────────────────────────                            │
│                                                                  │
│  1. 查询 ref_count = 0 的 blob                                    │
│     │                                                            │
│     ▼                                                            │
│  2. 移动到回收站 (trash/)                                          │
│     │                                                            │
│     ├── 记录移动时间                                               │
│     └── 更新 mtime                                                │
│     │                                                            │
│     ▼                                                            │
│  3. 回收站过期清理 (默认 24h)                                      │
│     │                                                            │
│     ├── 检查文件 mtime                                            │
│     └── 物理删除过期文件                                           │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## 5. 接口实现设计

### 5.1 LocalBlobStorage 实现

```go
package storage

import (
    "context"
    "os"
    "path/filepath"
)

type LocalBlobStorage struct {
    basePath string
}

func NewLocalBlobStorage(basePath string) *LocalBlobStorage {
    return &LocalBlobStorage{basePath: basePath}
}

func (s *LocalBlobStorage) Store(ctx context.Context, localPath, hash string) (string, error) {
    blobPath := s.getBlobPath(hash)
    
    // 检查是否已存在
    if _, err := os.Stat(blobPath); err == nil {
        return blobPath, nil // 去重
    }
    
    // 确保目录存在
    if err := os.MkdirAll(filepath.Dir(blobPath), 0755); err != nil {
        return "", err
    }
    
    // 写入临时文件
    tempPath := blobPath + ".uploading"
    if err := copyFile(localPath, tempPath); err != nil {
        return "", err
    }
    
    // 校验完整性
    actualHash, err := hashFile(tempPath)
    if err != nil || actualHash != hash {
        os.Remove(tempPath)
        return "", errors.New("hash mismatch")
    }
    
    // 原子重命名
    if err := os.Rename(tempPath, blobPath); err != nil {
        return "", err
    }
    
    return blobPath, nil
}

func (s *LocalBlobStorage) Exists(ctx context.Context, hash string) (bool, error) {
    _, err := os.Stat(s.getBlobPath(hash))
    return err == nil, nil
}

func (s *LocalBlobStorage) GetPath(ctx context.Context, hash string) (string, error) {
    return s.getBlobPath(hash), nil
}

func (s *LocalBlobStorage) getBlobPath(hash string) string {
    return filepath.Join(s.basePath, "blobs", hash[:2], hash[2:4], hash)
}
```

### 5.2 FileManager 实现

```go
package storage

import (
    "context"
    "database/sql"
)

type FileManagerImpl struct {
    db       *sql.DB
    blobStore BlobStorage
    basePath string
}

func NewFileManager(db *sql.DB, blobStore BlobStorage, basePath string) *FileManagerImpl {
    return &FileManagerImpl{
        db:        db,
        blobStore: blobStore,
        basePath:  basePath,
    }
}

func (m *FileManagerImpl) Upload(ctx context.Context, req *UploadRequest) (*UploadResult, error) {
    // 1. 计算文件哈希
    hash, err := hashFile(req.LocalPath)
    if err != nil {
        return nil, err
    }
    
    // 2. 存储 blob
    blobPath, err := m.blobStore.Store(ctx, req.LocalPath, hash)
    if err != nil {
        return nil, err
    }
    
    // 3. 数据库事务
    tx, err := m.db.BeginTx(ctx, nil)
    if err != nil {
        return nil, err
    }
    defer tx.Rollback()
    
    // 创建或获取 file 记录
    var fileID int64
    err = tx.QueryRowContext(ctx,
        "INSERT INTO files (user_name, path) VALUES ($1, $2) ON CONFLICT DO NOTHING RETURNING id",
        req.UserID, normalizePath(req.Path),
    ).Scan(&fileID)
    if err == sql.ErrNoRows {
        // 记录已存在，查询 ID
        err = tx.QueryRowContext(ctx,
            "SELECT id FROM files WHERE user_name = $1 AND path = $2 FOR UPDATE",
            req.UserID, normalizePath(req.Path),
        ).Scan(&fileID)
    }
    if err != nil {
        return nil, err
    }
    
    // 检查是否为新版本
    var latestVersion int
    var latestHash string
    err = tx.QueryRowContext(ctx,
        "SELECT version_num, hash FROM file_versions WHERE file_id = $1 ORDER BY version_num DESC LIMIT 1",
        fileID,
    ).Scan(&latestVersion, &latestHash)
    
    created := false
    version := latestVersion
    
    if err == sql.ErrNoRows || latestHash != hash {
        // 创建新版本
        version = latestVersion + 1
        if version == 0 {
            version = 1
        }
        _, err = tx.ExecContext(ctx,
            "INSERT INTO file_versions (file_id, version_num, hash, size, uploaded_by) VALUES ($1, $2, $3, $4, $5)",
            fileID, version, hash, getFileSize(req.LocalPath), req.UserID,
        )
        created = true
    }
    
    if err = tx.Commit(); err != nil {
        return nil, err
    }
    
    // 4. 更新用户目录软链接
    m.updateUserLink(req.UserID, req.Path, blobPath)
    
    return &UploadResult{
        Path:    req.Path,
        Hash:    hash,
        Version: version,
        Size:    getFileSize(req.LocalPath),
        Created: created,
    }, nil
}
```

---

## 6. 实现计划

### Phase 1：基础存储（预计 2 周）

| 任务 | 优先级 | 预计时间 |
|-----|-------|---------|
| 实现 LocalBlobStorage | P0 | 2 天 |
| 实现 FileManager 核心接口 | P0 | 3 天 |
| 数据库连接与迁移 | P0 | 2 天 |
| 用户目录软链接管理 | P0 | 2 天 |
| 单元测试 | P0 | 3 天 |

### Phase 2：版本管理（预计 1 周）

| 任务 | 优先级 | 预计时间 |
|-----|-------|---------|
| 实现 ManifestManager | P0 | 2 天 |
| 快照创建与锁定 | P0 | 2 天 |
| 引用计数视图 | P1 | 1 天 |
| 单元测试 | P0 | 2 天 |

### Phase 3：清理机制（预计 1 周）

| 任务 | 优先级 | 预计时间 |
|-----|-------|---------|
| 实现回收站机制 | P1 | 2 天 |
| 定时清理任务 | P1 | 2 天 |
| 引用计数清理 | P1 | 1 天 |
| 单元测试 | P0 | 2 天 |

### Phase 4：S3 支持（预计 1 周）

| 任务 | 优先级 | 预计时间 |
|-----|-------|---------|
| 实现 S3BlobStorage | P2 | 3 天 |
| 存储后端切换配置 | P2 | 1 天 |
| 预签名 URL 生成 | P2 | 1 天 |
| 单元测试 | P0 | 2 天 |

---

## 7. 关键技术细节

### 7.1 路径规范化

```go
// Windows 路径规范化规则：
// 1. 盘符大写，去除冒号：D:\projects → D/projects
// 2. 路径部分统一小写：D/Projects → D/projects
// 3. 分隔符统一为 /

func normalizePath(path string) string {
    path = strings.ReplaceAll(path, "\\", "/")
    
    // UNC 路径特殊处理
    if strings.HasPrefix(path, "//") {
        parts := strings.SplitN(path, "/", 5)
        if len(parts) >= 5 {
            return strings.Join(parts[:4], "/") + "/" + strings.ToLower(parts[4])
        }
        return path
    }
    
    // 本地路径
    if len(path) >= 2 && path[1] == ':' {
        return strings.ToUpper(string(path[0])) + strings.ToLower(path[2:])
    }
    
    return strings.ToLower(path)
}
```

### 7.2 增量哈希缓存

```go
// 本地缓存结构
type HashCache struct {
    MTime time.Time
    Size  int64
    Hash  string
}

// 优化：mtime + size 未变化则跳过哈希计算
func (m *FileManager) computeHash(path string, cache *HashCache) (string, error) {
    info, err := os.Stat(path)
    if err != nil {
        return "", err
    }
    
    // 缓存命中
    if cache != nil && 
       cache.MTime == info.ModTime() && 
       cache.Size == info.Size() {
        return cache.Hash, nil
    }
    
    // 重新计算
    hash, err := hashFile(path)
    if err != nil {
        return "", err
    }
    
    // 更新缓存
    return hash, nil
}
```

### 7.3 大文件分块上传

```go
// 大于 100MB 自动分块
const LargeFileThreshold = 100 * 1024 * 1024
const DefaultChunkSize = 64 * 1024 * 1024

func (m *FileManager) uploadLargeFile(ctx context.Context, path string) error {
    info, _ := os.Stat(path)
    if info.Size() < LargeFileThreshold {
        return m.uploadSmallFile(ctx, path)
    }
    
    // 分块上传
    chunks := (info.Size() + DefaultChunkSize - 1) / DefaultChunkSize
    
    for i := 0; i < chunks; i++ {
        offset := int64(i) * DefaultChunkSize
        size := min(DefaultChunkSize, info.Size()-offset)
        
        if err := m.uploadChunk(ctx, path, offset, size); err != nil {
            return err
        }
    }
    
    return m.mergeChunks(ctx, path)
}
```

---

## 8. 测试策略

### 8.1 单元测试

```go
func TestLocalBlobStorage_Store(t *testing.T) {
    // 测试去重
    // 测试哈希校验
    // 测试原子写入
}

func TestFileManager_Upload(t *testing.T) {
    // 测试新文件上传
    // 测试重复文件去重
    // 测试版本递增
    // 测试软链接更新
}

func TestManifestManager_Create(t *testing.T) {
    // 测试快照创建
    // 测试版本锁定
    // 测试只读保护
}
```

### 8.2 集成测试

```go
func TestUploadJobWorkflow(t *testing.T) {
    // 完整流程测试：
    // 1. 上传文件
    // 2. 创建任务快照
    // 3. 验证版本锁定
    // 4. 模拟文件更新
    // 5. 验证快照不受影响
}
```

---

## 9. 监控指标

| 指标 | 说明 |
|-----|-----|
| storage_blobs_total | blob 总数 |
| storage_blobs_size_bytes | blob 总大小 |
| storage_upload_count | 上传次数 |
| storage_upload_dedup_count | 去重次数 |
| storage_ref_count_zero | 零引用 blob 数量 |
| storage_trash_size_bytes | 回收站大小 |
