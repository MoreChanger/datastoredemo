"""
Manifest 管理模块 - 任务快照

核心功能：
- 创建任务快照（锁定文件版本）
- 快照查询与管理
- 状态跟踪

快照创建后，文件版本被锁定，不受后续上传影响。
"""

import json
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
from datetime import datetime

from .database import Database
from .file_manager import FileManager, normalize_windows_path
from .blob_storage import get_blob_path


@dataclass
class ManifestInfo:
    """快照信息"""
    manifest_id: int
    job_id: str
    user_name: str
    scene_path: Optional[str]
    output_path: Optional[str]
    status: str
    created_at: str
    file_count: int
    total_size: int


@dataclass
class ManifestEntry:
    """快照明细"""
    original_path: str
    hash: str
    version_num: int
    size: int


class ManifestManager:
    """
    快照管理器
    
    职责：
    - 创建任务快照
    - 锁定文件版本
    - 快照查询
    - 状态管理
    """
    
    def __init__(
        self,
        db: Database,
        file_manager: FileManager,
        jobs_path: str | Path,
        outputs_path: str | Path
    ):
        """
        初始化快照管理器
        
        Args:
            db: 数据库实例
            file_manager: 文件管理器实例
            jobs_path: Job 目录根路径
            outputs_path: 输出目录根路径
        """
        self.db = db
        self.file_manager = file_manager
        self.jobs_path = Path(jobs_path)
        self.outputs_path = Path(outputs_path)
    
    def init_directories(self) -> None:
        """初始化目录结构"""
        self.jobs_path.mkdir(parents=True, exist_ok=True)
        self.outputs_path.mkdir(parents=True, exist_ok=True)
    
    def create_manifest(
        self,
        user_name: str,
        file_paths: List[str],
        scene_path: Optional[str] = None,
        job_id: Optional[str] = None
    ) -> ManifestInfo:
        """
        创建任务快照
        
        流程：
        1. 生成 job_id
        2. 查询每个文件的最新版本
        3. 数据库事务（创建 manifest + entries）
        4. 创建 Job 目录和软链接
        5. 生成 manifest.json
        
        Args:
            user_name: 用户名
            file_paths: 文件路径列表（规范化后的路径）
            scene_path: 主场景文件路径（可选）
            job_id: 任务 ID（可选，不提供则自动生成）
        
        Returns:
            ManifestInfo 对象
        """
        # 1. 生成 job_id
        if job_id is None:
            job_id = f"job_{uuid.uuid4().hex[:12]}"
        
        output_path = str(self.outputs_path / job_id)
        
        # 收集涉及的盘符
        drives = set()
        for path in file_paths:
            if path and len(path) >= 1 and path[0].isalpha():
                drives.add(path[0].upper())
        
        # 2. 查询文件版本信息
        entries_data = []
        total_size = 0
        
        for path in file_paths:
            normalized_path = normalize_windows_path(path)
            
            # 查询文件最新版本
            row = self.db.fetchone(
                """SELECT f.id as file_id, fv.id as version_id, 
                          fv.version_num, fv.hash, fv.size
                   FROM files f
                   JOIN file_versions fv ON f.id = fv.file_id
                   WHERE f.user_name = ? AND f.path = ?
                   ORDER BY fv.version_num DESC LIMIT 1""",
                (user_name, normalized_path)
            )
            
            if row:
                entries_data.append({
                    "file_id": row["file_id"],
                    "version_id": row["version_id"],
                    "original_path": normalized_path,
                    "hash": row["hash"],
                    "version_num": row["version_num"],
                    "size": row["size"]
                })
                total_size += row["size"] or 0
        
        # 3. 数据库事务
        with self.db.transaction() as conn:
            # 创建 manifest 记录
            conn.execute(
                """INSERT INTO manifests
                   (job_id, user_name, scene_path, output_path, drives, status)
                   VALUES (?, ?, ?, ?, ?, 'submitted')""",
                (job_id, user_name, scene_path, output_path, json.dumps(list(drives)))
            )
            
            # 获取 manifest_id
            manifest_row = conn.execute(
                "SELECT id FROM manifests WHERE job_id = ?",
                (job_id,)
            ).fetchone()
            manifest_id = manifest_row["id"]
            
            # 创建 manifest_entries 记录
            for entry in entries_data:
                conn.execute(
                    """INSERT INTO manifest_entries
                       (manifest_id, file_id, file_version_id, original_path, hash)
                       VALUES (?, ?, ?, ?, ?)""",
                    (manifest_id, entry["file_id"], entry["version_id"],
                     entry["original_path"], entry["hash"])
                )
        
        # 4. 创建 Job 目录结构
        job_dir = self.jobs_path / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        
        # 创建文件链接（指向 blob）
        self._create_job_links(job_dir, entries_data)
        
        # 创建输出目录链接
        output_dir = self.outputs_path / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # 5. 生成 manifest.json
        manifest_json = {
            "job_id": job_id,
            "user_name": user_name,
            "scene_path": scene_path,
            "output_path": output_path,
            "drives": list(drives),
            "created_at": datetime.now().isoformat(),
            "status": "submitted",
            "files": [
                {
                    "path": e["original_path"],
                    "hash": e["hash"],
                    "version": e["version_num"],
                    "size": e["size"]
                }
                for e in entries_data
            ],
            "summary": {
                "file_count": len(entries_data),
                "total_size": total_size
            }
        }
        
        manifest_file = job_dir / "manifest.json"
        manifest_file.write_text(json.dumps(manifest_json, indent=2, ensure_ascii=False))
        
        return ManifestInfo(
            manifest_id=manifest_id,
            job_id=job_id,
            user_name=user_name,
            scene_path=scene_path,
            output_path=output_path,
            status="submitted",
            created_at=datetime.now().isoformat(),
            file_count=len(entries_data),
            total_size=total_size
        )
    
    def _create_job_links(self, job_dir: Path, entries: List[Dict]) -> None:
        """
        创建 Job 目录中的文件链接
        
        Args:
            job_dir: Job 目录路径
            entries: 文件条目列表
        """
        import platform
        import shutil
        
        for entry in entries:
            # 计算目标路径
            file_path = job_dir / entry["original_path"]
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 计算 blob 路径
            blob_path = get_blob_path(self.file_manager.blob_storage.base_path, entry["hash"])
            
            # 删除旧链接
            if file_path.exists() or file_path.is_symlink():
                file_path.unlink()
            
            # 创建链接
            system = platform.system()
            if system == "Windows":
                # Windows: 尝试软链接 -> 硬链接 -> 复制
                try:
                    import os
                    os.symlink(blob_path, file_path)
                    continue
                except (OSError, NotImplementedError):
                    pass
                
                try:
                    import os
                    os.link(blob_path, file_path)
                    continue
                except (OSError, NotImplementedError):
                    pass
                
                shutil.copy2(blob_path, file_path)
            else:
                import os
                os.symlink(blob_path, file_path)
    
    def get_manifest(self, job_id: str) -> Optional[ManifestInfo]:
        """
        获取快照信息
        
        Args:
            job_id: 任务 ID
        
        Returns:
            ManifestInfo 对象，不存在返回 None
        """
        row = self.db.fetchone(
            """SELECT m.*, 
                      (SELECT COUNT(*) FROM manifest_entries WHERE manifest_id = m.id) as file_count,
                      (SELECT COALESCE(SUM(fv.size), 0) 
                       FROM manifest_entries me 
                       JOIN file_versions fv ON fv.id = me.file_version_id 
                       WHERE me.manifest_id = m.id) as total_size
               FROM manifests m
               WHERE m.job_id = ?""",
            (job_id,)
        )
        
        if row is None:
            return None
        
        return ManifestInfo(
            manifest_id=row["id"],
            job_id=row["job_id"],
            user_name=row["user_name"],
            scene_path=row["scene_path"],
            output_path=row["output_path"],
            status=row["status"],
            created_at=row["created_at"],
            file_count=row["file_count"],
            total_size=row["total_size"]
        )
    
    def get_manifest_entries(self, job_id: str) -> List[ManifestEntry]:
        """
        获取快照明细
        
        Args:
            job_id: 任务 ID
        
        Returns:
            ManifestEntry 列表
        """
        rows = self.db.fetchall(
            """SELECT me.original_path, me.hash, fv.version_num, fv.size
               FROM manifest_entries me
               JOIN manifests m ON m.id = me.manifest_id
               JOIN file_versions fv ON fv.id = me.file_version_id
               WHERE m.job_id = ?
               ORDER BY me.original_path""",
            (job_id,)
        )
        
        return [
            ManifestEntry(
                original_path=row["original_path"],
                hash=row["hash"],
                version_num=row["version_num"],
                size=row["size"]
            )
            for row in rows
        ]
    
    def update_status(self, job_id: str, status: str) -> bool:
        """
        更新快照状态
        
        Args:
            job_id: 任务 ID
            status: 新状态
        
        Returns:
            是否更新成功
        """
        valid_statuses = ['submitted', 'rendering', 'completed', 'failed', 
                          'archiving', 'archived', 'deleted']
        
        if status not in valid_statuses:
            raise ValueError(f"Invalid status: {status}")
        
        with self.db.transaction() as conn:
            conn.execute(
                """UPDATE manifests 
                   SET status = ?, updated_at = datetime('now')
                   WHERE job_id = ?""",
                (status, job_id)
            )
            
            if status == 'archived':
                conn.execute(
                    """UPDATE manifests 
                       SET archived_at = datetime('now')
                       WHERE job_id = ?""",
                    (job_id,)
                )
        
        return True
    
    def list_manifests(
        self,
        user_name: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20
    ) -> List[ManifestInfo]:
        """
        列出快照
        
        Args:
            user_name: 用户名筛选（可选）
            status: 状态筛选（可选）
            limit: 返回数量限制
        
        Returns:
            ManifestInfo 列表
        """
        sql = """
            SELECT m.*, 
                   (SELECT COUNT(*) FROM manifest_entries WHERE manifest_id = m.id) as file_count,
                   (SELECT COALESCE(SUM(fv.size), 0) 
                    FROM manifest_entries me 
                    JOIN file_versions fv ON fv.id = me.file_version_id 
                    WHERE me.manifest_id = m.id) as total_size
            FROM manifests m
            WHERE 1=1
        """
        params = []
        
        if user_name:
            sql += " AND m.user_name = ?"
            params.append(user_name)
        
        if status:
            sql += " AND m.status = ?"
            params.append(status)
        
        sql += " ORDER BY m.created_at DESC LIMIT ?"
        params.append(limit)
        
        rows = self.db.fetchall(sql, tuple(params))
        
        return [
            ManifestInfo(
                manifest_id=row["id"],
                job_id=row["job_id"],
                user_name=row["user_name"],
                scene_path=row["scene_path"],
                output_path=row["output_path"],
                status=row["status"],
                created_at=row["created_at"],
                file_count=row["file_count"],
                total_size=row["total_size"]
            )
            for row in rows
        ]
    
    def get_manifest_stats(self) -> Dict[str, Any]:
        """获取快照统计"""
        total_count = self.db.fetchone(
            "SELECT COUNT(*) as count FROM manifests"
        )["count"]
        
        active_count = self.db.fetchone(
            "SELECT COUNT(*) as count FROM manifests WHERE status NOT IN ('archived', 'deleted')"
        )["count"]
        
        status_counts = self.db.fetchall(
            "SELECT status, COUNT(*) as count FROM manifests GROUP BY status"
        )
        
        return {
            "total_manifests": total_count,
            "active_manifests": active_count,
            "archived_manifests": total_count - active_count,
            "by_status": {row["status"]: row["count"] for row in status_counts}
        }
