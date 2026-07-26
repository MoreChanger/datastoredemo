"""
LoongClouds Storage Module

内容寻址存储系统 demo

核心模块：
- BlobStorage: SHA-256 内容寻址存储
- FileManager: 文件上传与版本管理
- Database: 元数据存储（支持 SQLite 和 PostgreSQL）
- HashCache: 本地哈希缓存优化
- ManifestManager: 任务快照管理
- StorageCleaner: 引用计数与清理
"""

from .blob_storage import BlobStorage, compute_sha256, get_blob_path
from .db import Database, DatabaseConfig, DatabaseType, DatabaseBackend
from .file_manager import FileManager, normalize_windows_path, UploadResult, FileInfo
from .hash_cache import HashCache, CacheEntry
from .manifest_manager import ManifestManager, ManifestInfo, ManifestEntry
from .cleaner import StorageCleaner, RefCountInfo

# 向后兼容：保留旧的 Database 类（SQLite only）
from . import database as legacy_database

__all__ = [
    "BlobStorage",
    "compute_sha256",
    "get_blob_path",
    "Database",
    "DatabaseConfig",
    "DatabaseType",
    "DatabaseBackend",
    "FileManager",
    "normalize_windows_path",
    "UploadResult",
    "FileInfo",
    "HashCache",
    "CacheEntry",
    "ManifestManager",
    "ManifestInfo",
    "ManifestEntry",
    "StorageCleaner",
    "RefCountInfo",
    "legacy_database",
]

__version__ = "1.0.0"
