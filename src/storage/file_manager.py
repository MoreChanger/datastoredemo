"""
文件管理模块 - 上传与版本管理

核心功能：
- 文件上传（哈希计算 -> blob 存储 -> 版本记录）
- 版本历史追踪
- 用户目录软链接管理
- 本地哈希缓存优化
"""

import os
import re
import json
import shutil
import platform
from pathlib import Path
from typing import Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

from .blob_storage import BlobStorage, compute_sha256, get_blob_path
from .db import Database
from .hash_cache import HashCache


def normalize_windows_path(windows_path: str) -> str:
    """
    将 Windows 路径规范化为存储路径
    
    规则：
    - 盘符统一转大写，去除冒号（D: → D）
    - 路径部分统一转小写
    - 反斜杠统一为正斜杠
    - 去除多余分隔符
    - 支持 UNC 路径
    
    Args:
        windows_path: Windows 路径（如 D:\\Projects\\shot_01\\file.txt）
    
    Returns:
        规范化后的路径（如 D/projects/shot_01/file.txt）
    """
    path = windows_path.replace("\\", "/")
    
    # 处理 UNC 路径
    if path.startswith("//"):
        path = "//" + re.sub(r"/+", "/", path[2:])
        parts = path.split("/")
        if len(parts) > 4:
            unc_prefix = "/".join(parts[:4])
            remaining = "/".join(parts[4:])
            return unc_prefix + "/" + remaining.lower()
        return path
    
    # 合并连续斜杠
    path = re.sub(r"/+", "/", path)
    
    # 盘符处理
    # 情况1: D:\path 或 D:/path (带冒号)
    if len(path) >= 2 and path[1] == ":":
        path = path[0].upper() + path[2:].lower()
    # 情况2: D/path (已规范化格式，盘符后直接是斜杠)
    elif len(path) >= 2 and path[0].isalpha() and path[1] == "/":
        path = path[0].upper() + path[1:].lower()
    else:
        # 相对路径或其他格式：整体转小写
        path = path.lower()
    
    return path


@dataclass
class UploadResult:
    """上传结果"""
    path: str           # 规范化后的路径
    hash: str           # SHA-256 哈希值
    version: int        # 版本号
    size: int           # 文件大小（字节）
    created: bool       # 是否创建了新版本
    deduplicated: bool  # blob 是否去重
    cache_hit: bool     # 哈希缓存是否命中


@dataclass
class FileInfo:
    """文件信息"""
    file_id: int
    path: str
    status: str
    current_version: int
    current_hash: str
    current_size: int
    created_at: str
    updated_at: Optional[str]


class FileManager:
    """
    文件管理器
    
    职责：
    - 文件上传流程
    - 版本管理
    - 用户目录软链接维护
    - 哈希缓存优化
    """
    
    def __init__(
        self,
        db: Database,
        blob_storage: BlobStorage,
        users_path: str | Path,
        hash_cache: Optional[HashCache] = None
    ):
        """
        初始化文件管理器
        
        Args:
            db: 数据库实例
            blob_storage: Blob 存储实例
            users_path: 用户目录根路径
            hash_cache: 哈希缓存实例（可选，不提供则不使用缓存）
        """
        self.db = db
        self.blob_storage = blob_storage
        self.users_path = Path(users_path)
        self.hash_cache = hash_cache
    
    def init_directories(self) -> None:
        """初始化目录结构"""
        self.blob_storage.init_storage()
        self.users_path.mkdir(parents=True, exist_ok=True)
    
    def upload_file(
        self,
        local_path: str | Path,
        user_name: str,
        server_path: Optional[str] = None
    ) -> UploadResult:
        """
        上传文件
        
        流程：
        1. 计算文件哈希
        2. 存储 blob（去重）
        3. 数据库事务（创建/更新文件和版本记录）
        4. 更新用户目录软链接
        
        Args:
            local_path: 本地文件路径
            user_name: 用户名
            server_path: 服务器存储路径（可选，默认使用 local_path 的规范化形式）
        
        Returns:
            UploadResult 对象
        
        Raises:
            FileNotFoundError: 源文件不存在
        """
        local_path = Path(local_path)
        
        if not local_path.exists():
            raise FileNotFoundError(f"Local file not found: {local_path}")
        
        # 获取文件元信息
        file_stat = local_path.stat()
        file_size = file_stat.st_size
        file_mtime = file_stat.st_mtime
        
        # 规范化路径
        if server_path is None:
            server_path = normalize_windows_path(str(local_path))
        else:
            server_path = normalize_windows_path(server_path)
        
        # 1. 尝试从缓存获取哈希
        file_hash = None
        cache_hit = False
        
        if self.hash_cache:
            cached_hash = self.hash_cache.get(
                path=server_path,
                mtime=file_mtime,
                size=file_size
            )
            
            if cached_hash:
                # 缓存命中
                file_hash = cached_hash
                cache_hit = True
                # 后置校验：检测哈希计算期间文件是否被修改
                post_mtime = local_path.stat().st_mtime
                post_size = local_path.stat().st_size
                if post_mtime != file_mtime or post_size != file_size:
                    # 文件已变化，缓存不可靠，重新计算
                    file_hash = None
                    cache_hit = False
        
        # 2. 计算哈希（如果缓存未命中）
        if file_hash is None:
            file_hash = compute_sha256(local_path)
            cache_hit = False
            
            # 更新缓存
            if self.hash_cache:
                # 重新获取最新的 mtime（计算后）
                file_mtime = local_path.stat().st_mtime
                self.hash_cache.set(
                    path=server_path,
                    mtime=file_mtime,
                    size=file_size,
                    hash=file_hash
                )
        
        # 3. 存储 blob
        blob_path, blob_created = self.blob_storage.store(local_path, file_hash)
        deduplicated = not blob_created  # 不是新创建 = 去重
        
        # 4. 数据库事务（带并发竞态防护）
        # 使用 lock_row 提供原子性和隔离性（PostgreSQL: SELECT FOR UPDATE, SQLite: BEGIN IMMEDIATE）
        with self.db.lock_row("files", "user_name = ? AND path = ?", (user_name, server_path)):
            # 使用 UPSERT 创建或获取 files 记录（防止并发竞态）
            file_id = self.db.upsert(
                "files",
                {
                    "user_name": user_name,
                    "path": server_path,
                    "status": "active",
                },
                conflict_columns=["user_name", "path"]
            )
            
            # 查询最新版本
            latest_row = self.db.fetchone(
                """SELECT version_num, hash FROM file_versions
                   WHERE file_id = ?
                   ORDER BY version_num DESC LIMIT 1""",
                (file_id,)
            )
            
            # 判断是否为新版本
            if latest_row is None:
                # 首次上传，插入版本记录
                version_num = 1
                created = True
                self.db.execute(
                    """INSERT INTO file_versions
                       (file_id, version_num, hash, size, uploaded_by)
                       VALUES (?, ?, ?, ?, ?)""",
                    (file_id, version_num, file_hash, file_size, user_name)
                )
            elif latest_row["hash"] == file_hash:
                # 内容相同，复用版本
                version_num = latest_row["version_num"]
                created = False
            else:
                # 内容不同，新版本
                version_num = latest_row["version_num"] + 1
                created = True
                self.db.execute(
                    """INSERT INTO file_versions
                       (file_id, version_num, hash, size, uploaded_by)
                       VALUES (?, ?, ?, ?, ?)""",
                    (file_id, version_num, file_hash, file_size, user_name)
                )
        
        # 5. 更新用户目录软链接/复制文件
        self._update_user_link(user_name, server_path, blob_path)
        
        # 6. 保存缓存
        if self.hash_cache:
            self.hash_cache.save()
        
        return UploadResult(
            path=server_path,
            hash=file_hash,
            version=version_num,
            size=file_size,
            created=created,
            deduplicated=deduplicated,
            cache_hit=cache_hit
        )
    
    def _update_user_link(
        self,
        user_name: str,
        server_path: str,
        blob_path: Path
    ) -> None:
        """
        更新用户目录链接
        
        在 Windows 上尝试以下方式（按优先级）：
        1. 软链接（需要管理员权限或开发者模式）
        2. 硬链接（需要同一文件系统）
        3. 文件复制（兼容性最好）
        
        在 Linux/macOS 上使用软链接。
        
        Args:
            user_name: 用户名
            server_path: 规范化后的路径
            blob_path: blob 文件路径
        """
        user_file_path = self.users_path / user_name / server_path
        
        # 确保目录存在
        user_file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 删除旧文件（如果存在）
        if user_file_path.exists() or user_file_path.is_symlink():
            user_file_path.unlink()
        
        # 根据操作系统选择链接方式
        system = platform.system()
        
        if system == "Windows":
            # Windows: 尝试软链接 -> 硬链接 -> 复制
            try:
                # 尝试创建软链接（需要管理员权限或开发者模式 Win10+）
                os.symlink(blob_path, user_file_path)
                return
            except (OSError, NotImplementedError):
                pass
            
            try:
                # 尝试创建硬链接（需要同一文件系统）
                os.link(blob_path, user_file_path)
                return
            except (OSError, NotImplementedError):
                pass
            
            # 回退到复制
            shutil.copy2(blob_path, user_file_path)
        else:
            # Linux/macOS: 使用软链接
            os.symlink(blob_path, user_file_path)
    
    def get_file_info(self, user_name: str, path: str) -> Optional[FileInfo]:
        """
        获取文件信息
        
        Args:
            user_name: 用户名
            path: 文件路径（原始或规范化）
        
        Returns:
            FileInfo 对象，不存在返回 None
        """
        normalized_path = normalize_windows_path(path)
        
        row = self.db.fetchone(
            """SELECT f.id, f.path, f.status, f.created_at, f.updated_at,
                      fv.version_num, fv.hash, fv.size
               FROM files f
               LEFT JOIN file_versions fv ON f.id = fv.file_id
               WHERE f.user_name = ? AND f.path = ?
               ORDER BY fv.version_num DESC LIMIT 1""",
            (user_name, normalized_path)
        )
        
        if row is None:
            return None
        
        return FileInfo(
            file_id=row["id"],
            path=row["path"],
            status=row["status"],
            current_version=row["version_num"] or 0,
            current_hash=row["hash"] or "",
            current_size=row["size"] or 0,
            created_at=row["created_at"],
            updated_at=row["updated_at"]
        )
    
    def get_version_history(
        self,
        user_name: str,
        path: str,
        limit: int = 10
    ) -> list[dict]:
        """
        获取文件版本历史
        
        Args:
            user_name: 用户名
            path: 文件路径
            limit: 返回版本数量限制
        
        Returns:
            版本列表（按版本号降序）
        """
        normalized_path = normalize_windows_path(path)
        
        rows = self.db.fetchall(
            """SELECT fv.version_num, fv.hash, fv.size, fv.uploaded_by, fv.uploaded_at
               FROM file_versions fv
               JOIN files f ON f.id = fv.file_id
               WHERE f.user_name = ? AND f.path = ?
               ORDER BY fv.version_num DESC
               LIMIT ?""",
            (user_name, normalized_path, limit)
        )
        
        return [dict(row) for row in rows]
    
    def list_user_files(self, user_name: str, status: str = "active") -> list[dict]:
        """
        列出用户的所有文件
        
        Args:
            user_name: 用户名
            status: 文件状态筛选
        
        Returns:
            文件列表
        """
        rows = self.db.fetchall(
            """SELECT f.id, f.path, f.status, f.created_at,
                      fv.version_num, fv.hash, fv.size
               FROM files f
               LEFT JOIN file_versions fv ON f.id = fv.file_id
               WHERE f.user_name = ? AND f.status = ?
               ORDER BY f.path""",
            (user_name, status)
        )
        
        # 过滤，只保留每个文件的最新版本
        result = {}
        for row in rows:
            path = row["path"]
            if path not in result or (row["version_num"] or 0) > result[path]["version_num"]:
                result[path] = dict(row)
        
        return list(result.values())
    
    def get_storage_stats(self) -> dict:
        """获取存储统计信息"""
        stats = self.blob_storage.get_stats()
        
        # 数据库统计
        file_count = self.db.fetchone(
            "SELECT COUNT(*) as count FROM files WHERE status = 'active'"
        )["count"]
        
        version_count = self.db.fetchone(
            "SELECT COUNT(*) as count FROM file_versions"
        )["count"]
        
        stats.update({
            "file_count": file_count,
            "version_count": version_count,
        })
        
        return stats
