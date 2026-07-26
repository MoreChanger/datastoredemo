"""
清理模块 - 引用计数与 blob 清理

核心功能：
- 引用计数查询
- 零引用 blob 移至回收站
- 回收站过期清理
"""

import os
import shutil
import time
from pathlib import Path
from typing import Dict, List, Set, Optional
from dataclasses import dataclass

from .db import Database
from .blob_storage import BlobStorage, get_blob_path


@dataclass
class RefCountInfo:
    """引用计数信息"""
    hash: str
    ref_count: int
    job_refs: int
    user_refs: int
    blob_exists: bool


class StorageCleaner:
    """
    存储清理器
    
    职责：
    - 计算引用计数
    - 清理零引用 blob
    - 回收站管理
    """
    
    def __init__(
        self,
        db: Database,
        blob_storage: BlobStorage,
        trash_path: str | Path,
        grace_hours: int = 24
    ):
        """
        初始化清理器
        
        Args:
            db: 数据库实例
            blob_storage: Blob 存储实例
            trash_path: 回收站路径
            grace_hours: 回收站保留时间（小时）
        """
        self.db = db
        self.blob_storage = blob_storage
        self.trash_path = Path(trash_path)
        self.grace_hours = grace_hours
    
    def init_trash(self) -> None:
        """初始化回收站目录"""
        self.trash_path.mkdir(parents=True, exist_ok=True)
    
    def get_ref_counts(self) -> Dict[str, RefCountInfo]:
        """
        计算所有 blob 的引用计数
        
        引用来源：
        1. 活跃 Job 引用（status NOT IN ('archived', 'deleted')）
        2. 用户目录最新版本引用（files.status != 'deleted'）
        
        Returns:
            {hash: RefCountInfo} 字典
        """
        # 活跃 Job 引用
        job_refs = self.db.fetchall(
            """SELECT me.hash, COUNT(*) as count
               FROM manifest_entries me
               JOIN manifests m ON m.id = me.manifest_id
               WHERE m.status NOT IN ('archived', 'deleted')
               GROUP BY me.hash"""
        )
        
        job_ref_map = {row["hash"]: row["count"] for row in job_refs}
        
        # 用户目录最新版本引用
        # 使用子查询获取每个文件的最新版本
        user_refs = self.db.fetchall(
            """SELECT fv.hash, COUNT(*) as count
               FROM file_versions fv
               JOIN files f ON f.id = fv.file_id
               WHERE f.status != 'deleted'
               AND fv.id = (
                   SELECT id FROM file_versions 
                   WHERE file_id = fv.file_id 
                   ORDER BY version_num DESC LIMIT 1
               )
               GROUP BY fv.hash"""
        )
        
        user_ref_map = {row["hash"]: row["count"] for row in user_refs}
        
        # 合并所有哈希
        all_hashes = set(job_ref_map.keys()) | set(user_ref_map.keys())
        
        result = {}
        for hash_val in all_hashes:
            job_count = job_ref_map.get(hash_val, 0)
            user_count = user_ref_map.get(hash_val, 0)
            
            result[hash_val] = RefCountInfo(
                hash=hash_val,
                ref_count=job_count + user_count,
                job_refs=job_count,
                user_refs=user_count,
                blob_exists=self.blob_storage.exists(hash_val)
            )
        
        return result
    
    def get_zero_ref_hashes(self) -> List[str]:
        """
        获取零引用的 blob 哈希列表
        
        扫描 blob 存储中实际存在的文件，检查引用计数。
        
        Returns:
            零引用哈希列表
        """
        ref_counts = self.get_ref_counts()
        
        # 扫描 blob 存储中所有存在的 blob
        zero_refs = []
        blobs_path = self.blob_storage.blobs_path
        
        if not blobs_path.exists():
            return []
        
        for hash_dir in blobs_path.iterdir():
            if hash_dir.name == ".uploading":
                continue
            if not hash_dir.is_dir():
                continue
            
            for sub_dir in hash_dir.iterdir():
                if not sub_dir.is_dir():
                    continue
                
                for blob_file in sub_dir.iterdir():
                    if not blob_file.is_file():
                        continue
                    
                    hash_val = blob_file.name
                    if len(hash_val) != 64:
                        continue
                    
                    # 检查引用计数
                    if hash_val not in ref_counts:
                        # 哈希不在引用计数中 = 零引用
                        zero_refs.append(hash_val)
                    elif ref_counts[hash_val].ref_count == 0:
                        zero_refs.append(hash_val)
        
        return zero_refs
    
    def move_to_trash(self, hash_val: str) -> bool:
        """
        将 blob 移至回收站
        
        Args:
            hash_val: SHA-256 哈希值
        
        Returns:
            是否成功移动
        """
        blob_path = get_blob_path(self.blob_storage.base_path, hash_val)
        
        if not blob_path.exists():
            return False
        
        # 计算回收站路径
        trash_blob_path = self.trash_path / hash_val[:2] / hash_val[2:4] / hash_val
        trash_blob_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            # 原子移动
            shutil.move(str(blob_path), str(trash_blob_path))
            return True
        except Exception as e:
            print(f"Error moving blob to trash: {e}")
            return False
    
    def restore_from_trash(self, hash_val: str) -> bool:
        """
        从回收站恢复 blob
        
        Args:
            hash_val: SHA-256 哈希值
        
        Returns:
            是否成功恢复
        """
        trash_blob_path = self.trash_path / hash_val[:2] / hash_val[2:4] / hash_val
        blob_path = get_blob_path(self.blob_storage.base_path, hash_val)
        
        if not trash_blob_path.exists():
            return False
        
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            shutil.move(str(trash_blob_path), str(blob_path))
            return True
        except Exception as e:
            print(f"Error restoring blob from trash: {e}")
            return False
    
    def cleanup_zero_refs(self, dry_run: bool = False) -> Dict[str, int]:
        """
        清理零引用 blob
        
        Args:
            dry_run: 是否为演练模式（不实际移动文件）
        
        Returns:
            清理统计 {moved: count, failed: count}
        """
        zero_refs = self.get_zero_ref_hashes()
        
        stats = {"moved": 0, "failed": 0}
        
        if dry_run:
            stats["would_move"] = len(zero_refs)
            return stats
        
        for hash_val in zero_refs:
            if self.move_to_trash(hash_val):
                stats["moved"] += 1
            else:
                stats["failed"] += 1
        
        return stats
    
    def empty_trash(self, force: bool = False) -> Dict[str, int]:
        """
        清空回收站
        
        Args:
            force: 是否强制清空（忽略保留时间）
        
        Returns:
            清理统计 {deleted: count, kept: count, errors: count}
        """
        stats = {"deleted": 0, "kept": 0, "errors": 0}
        
        if not self.trash_path.exists():
            return stats
        
        now = time.time()
        grace_seconds = self.grace_hours * 3600
        
        for hash_dir in self.trash_path.iterdir():
            if not hash_dir.is_dir():
                continue
            
            for sub_dir in hash_dir.iterdir():
                if not sub_dir.is_dir():
                    continue
                
                for blob_file in sub_dir.iterdir():
                    if not blob_file.is_file():
                        continue
                    
                    # 检查文件年龄
                    file_mtime = blob_file.stat().st_mtime
                    file_age = now - file_mtime
                    
                    if force or file_age > grace_seconds:
                        # 删除文件
                        try:
                            blob_file.unlink()
                            stats["deleted"] += 1
                        except Exception:
                            stats["errors"] += 1
                    else:
                        stats["kept"] += 1
        
        return stats
    
    def get_trash_stats(self) -> Dict[str, any]:
        """
        获取回收站统计
        
        Returns:
            统计信息字典
        """
        if not self.trash_path.exists():
            return {
                "file_count": 0,
                "total_size": 0,
                "oldest_age_hours": 0
            }
        
        file_count = 0
        total_size = 0
        oldest_mtime = time.time()
        
        for hash_dir in self.trash_path.iterdir():
            if not hash_dir.is_dir():
                continue
            
            for sub_dir in hash_dir.iterdir():
                if not sub_dir.is_dir():
                    continue
                
                for blob_file in sub_dir.iterdir():
                    if blob_file.is_file():
                        file_count += 1
                        total_size += blob_file.stat().st_size
                        oldest_mtime = min(oldest_mtime, blob_file.stat().st_mtime)
        
        oldest_age_hours = (time.time() - oldest_mtime) / 3600 if file_count > 0 else 0
        
        return {
            "file_count": file_count,
            "total_size": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "oldest_age_hours": round(oldest_age_hours, 1),
            "grace_hours": self.grace_hours
        }
    
    def get_cleanup_report(self) -> Dict[str, any]:
        """
        生成清理报告
        
        Returns:
            完整的清理报告
        """
        ref_counts = self.get_ref_counts()
        
        zero_ref_count = sum(1 for info in ref_counts.values() if info.ref_count == 0)
        active_ref_count = sum(1 for info in ref_counts.values() if info.ref_count > 0)
        
        total_blob_count = 0
        total_blob_size = 0
        
        blobs_path = self.blob_storage.blobs_path
        if blobs_path.exists():
            for hash_dir in blobs_path.iterdir():
                if hash_dir.name == ".uploading":
                    continue
                if not hash_dir.is_dir():
                    continue
                
                for sub_dir in hash_dir.iterdir():
                    if not sub_dir.is_dir():
                        continue
                    
                    for blob_file in sub_dir.iterdir():
                        if blob_file.is_file() and len(blob_file.name) == 64:
                            total_blob_count += 1
                            total_blob_size += blob_file.stat().st_size
        
        return {
            "blobs": {
                "total_count": total_blob_count,
                "active_count": active_ref_count,
                "zero_ref_count": zero_ref_count,
                "total_size_mb": round(total_blob_size / (1024 * 1024), 2)
            },
            "trash": self.get_trash_stats(),
            "recommendations": {
                "can_cleanup": zero_ref_count > 0,
                "can_empty_trash": self.get_trash_stats()["file_count"] > 0
            }
        }
