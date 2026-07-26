# LoongClouds 存储系统

生产级内容寻址存储（Content-Addressable Storage）系统，用于渲染农场存储架构。

## 功能特性

### P0 - 核心存储
- **SHA-256 Blob 存储** - 内容寻址，自动去重
- **原子写入** - 临时文件 → 校验 → 重命名
- **版本管理** - 自动记录文件版本历史
- **路径规范化** - Windows 路径统一处理

### P1 - 性能优化
- **哈希缓存** - mtime + size 缓存，跳过未修改文件的哈希计算
- **用户目录链接** - 自动创建文件链接/副本

### P2 - 快照与清理
- **Manifest 快照** - 任务提交时锁定文件版本
- **引用计数** - 自动追踪 blob 引用
- **回收站机制** - 安全清理零引用 blob

### P3 - 生产级架构
- **PostgreSQL 支持** - 连接池、WAL、行级锁
- **并发安全** - UPSERT、SELECT FOR UPDATE、事务隔离
- **SQLite 兼容** - 开发测试友好，无缝切换

## 项目结构

```
datastoredemo/
├── src/storage/              # 核心存储模块
│   ├── blob_storage.py       # SHA-256 blob 存储
│   ├── file_manager.py       # 文件上传与版本管理
│   ├── database.py           # SQLite 元数据存储（向后兼容）
│   ├── db/                   # 数据库抽象层
│   │   └── __init__.py       # PostgreSQL + SQLite 统一接口
│   ├── hash_cache.py         # 本地哈希缓存
│   ├── manifest_manager.py   # 任务快照管理
│   └── cleaner.py            # 引用计数清理
├── scripts/                  # 数据库脚本
│   └── init.sql              # PostgreSQL Schema
├── tests/                    # 单元测试 (64 tests)
├── docs/                     # 设计文档
│   ├── 04-storage-system-design.md
│   └── storage-module-design.md
├── data/                     # 运行时数据
│   ├── storage/              # Blob 存储
│   ├── users/                # 用户目录
│   ├── jobs/                 # Job 快照
│   ├── outputs/              # 渲染输出
│   ├── trash/                # 回收站
│   └── storage.db            # SQLite 数据库
├── docker-compose.yml        # PostgreSQL 容器配置
├── demo.py                   # Demo CLI
└── requirements.txt
```

## 快速开始

### 环境要求
- Python 3.10+
- 无外部依赖（使用 Python 标准库）

### 安装

```bash
# 克隆项目
git clone <repo-url>
cd datastoredemo

# 安装测试依赖（可选）
pip install pytest
```

### 运行 Demo

```bash
# 初始化存储
python demo.py init

# 上传文件
python demo.py upload myfile.txt D/projects/myfile.txt

# 查看文件信息
python demo.py info D/projects/myfile.txt

# 查看版本历史
python demo.py history D/projects/myfile.txt

# 创建任务快照
python demo.py job-create D/projects/file1.txt D/projects/file2.txt

# 查看存储统计
python demo.py stats
```

## 使用方法

### 基础操作

```bash
# 初始化存储系统
python demo.py init

# 上传文件（自动版本管理）
python demo.py upload <local_file> [server_path]

# 查看文件信息
python demo.py info <path>

# 查看版本历史
python demo.py history <path>

# 列出所有文件
python demo.py list

# 查看存储统计
python demo.py stats
```

### 缓存管理

```bash
# 查看缓存统计
python demo.py cache-stats

# 清空缓存
python demo.py cache-clear
```

### 快照管理

```bash
# 创建快照（锁定文件版本）
python demo.py job-create <path>... [--scene <scene_path>] [--job-id <id>]

# 查看快照信息
python demo.py job-info <job_id>

# 列出所有快照
python demo.py job-list [--user <user>] [--status <status>]
```

### 清理管理

```bash
# 查看清理报告
python demo.py cleanup-report

# 演练清理（不实际删除）
python demo.py cleanup --dry-run

# 执行清理
python demo.py cleanup
```

## API 参考

### BlobStorage

```python
from src.storage import BlobStorage, compute_sha256

# 创建存储实例
storage = BlobStorage("/path/to/storage")
storage.init_storage()

# 存储文件
blob_path, created = storage.store("/path/to/file.txt")

# 检查是否存在
exists = storage.exists(hash_value)

# 获取统计
stats = storage.get_stats()
```

### FileManager

```python
from src.storage import FileManager, BlobStorage, Database, HashCache

# 创建管理器
db = Database("/path/to/db.db")
blob_storage = BlobStorage("/path/to/storage")
hash_cache = HashCache("/path/to/cache.json")
fm = FileManager(db, blob_storage, "/path/to/users", hash_cache)

# 上传文件
result = fm.upload_file(
    local_path="/path/to/local/file.txt",
    user_name="user1",
    server_path="D/projects/file.txt"
)

# 获取文件信息
info = fm.get_file_info("user1", "D/projects/file.txt")

# 获取版本历史
history = fm.get_version_history("user1", "D/projects/file.txt")
```

### ManifestManager

```python
from src.storage import ManifestManager

# 创建快照管理器
mm = ManifestManager(db, fm, "/path/to/jobs", "/path/to/outputs")

# 创建快照
manifest = mm.create_manifest(
    user_name="user1",
    file_paths=["D/projects/file1.txt", "D/projects/file2.txt"],
    scene_path="D/projects/scene.ma"
)

# 获取快照信息
info = mm.get_manifest("job_xxx")

# 更新状态
mm.update_status("job_xxx", "rendering")
```

### StorageCleaner

```python
from src.storage import StorageCleaner

# 创建清理器
cleaner = StorageCleaner(db, blob_storage, "/path/to/trash")

# 获取引用计数
ref_counts = cleaner.get_ref_counts()

# 获取零引用 blob
zero_refs = cleaner.get_zero_ref_hashes()

# 清理零引用 blob
stats = cleaner.cleanup_zero_refs(dry_run=True)

# 获取清理报告
report = cleaner.get_cleanup_report()
```

## 测试

```bash
# 运行所有测试
python -m pytest tests/ -v

# 运行特定测试
python -m pytest tests/test_blob_storage.py -v

# 测试覆盖率
pip install pytest-cov
python -m pytest tests/ --cov=src/storage
```

### 测试统计

| 阶段 | 测试数量 | 覆盖功能 |
|------|----------|----------|
| P0 | 16 | Blob 存储、文件上传 |
| P1 | 18 | 哈希缓存、用户目录 |
| P2 | 17 | 快照、引用计数清理 |
| 其他 | 13 | 路径规范化等 |
| **总计** | **64** | |

## 设计文档

详细设计文档位于 `docs/` 目录：

- [`04-storage-system-design.md`](docs/04-storage-system-design.md) - 完整系统设计 v4.4
- [`storage-module-design.md`](docs/storage-module-design.md) - 存储模块设计 v1.0

## 核心概念

### 内容寻址存储

所有文件以 **SHA-256 哈希值** 作为唯一标识，相同内容只存储一份。

```
/blobs/{hash[:2]}/{hash[2:4]}/{hash}
```

### 版本管理

每次上传内容不同的文件自动创建新版本：

```
file.txt (v1) → hash: abc123...
file.txt (v2) → hash: def456...  (内容变化)
file.txt (v3) → hash: def456...  (内容未变，复用 v2)
```

### 快照锁定

任务提交时创建快照，锁定文件版本，确保可复现：

```json
{
  "job_id": "job_xxx",
  "files": [
    {"path": "D/projects/file.txt", "version": 2, "hash": "def456..."}
  ]
}
```

### 引用计数

blob 引用来源：
1. **活跃 Job 引用** - manifest 中的文件
2. **用户目录引用** - 每个文件的最新版本

零引用 blob 自动清理到回收站。

## 路径规范化

Windows 路径统一规范化处理：

```
D:\Projects\file.txt → D/projects/file.txt
d:\projects\FILE.TXT → D/projects/file.txt
```

规则：
- 盘符大写，去除冒号
- 路径部分小写
- 分隔符统一为 `/`

## 技术栈

- **语言**: Python 3.10+
- **数据库**: SQLite（演示）/ PostgreSQL（生产兼容）
- **存储**: 本地文件系统（演示）/ S3（生产支持）

## 许可证

MIT License
