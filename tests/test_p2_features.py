"""
单元测试 - Manifest 快照与清理

测试内容：
- Manifest 创建与管理
- 引用计数计算
- 清理机制
"""

import os
import time
import tempfile
import pytest
from pathlib import Path

from src.storage import (
    ManifestManager,
    StorageCleaner,
    FileManager,
    BlobStorage,
    Database,
    HashCache,
)


class TestManifestManager:
    """测试快照管理"""
    
    @pytest.fixture
    def setup_env(self, tmp_path):
        """创建测试环境"""
        storage_path = tmp_path / "storage"
        users_path = tmp_path / "users"
        jobs_path = tmp_path / "jobs"
        outputs_path = tmp_path / "outputs"
        db_path = tmp_path / "test.db"
        cache_path = tmp_path / "hash_cache.json"
        
        blob_storage = BlobStorage(storage_path)
        blob_storage.init_storage()
        
        db = Database(db_path)
        db.init_schema()
        
        hash_cache = HashCache(cache_path)
        
        file_manager = FileManager(db, blob_storage, users_path, hash_cache)
        file_manager.init_directories()
        
        manifest_manager = ManifestManager(db, file_manager, jobs_path, outputs_path)
        manifest_manager.init_directories()
        
        return {
            "manifest_manager": manifest_manager,
            "file_manager": file_manager,
            "blob_storage": blob_storage,
            "db": db,
            "jobs_path": jobs_path,
            "outputs_path": outputs_path,
            "tmp_path": tmp_path,
        }
    
    @pytest.fixture
    def test_files(self, tmp_path):
        """创建测试文件"""
        files = []
        for i in range(3):
            f = tmp_path / f"file{i}.txt"
            f.write_bytes(f"content {i}\n".encode())
            files.append(f)
        return files
    
    def test_create_manifest(self, setup_env, test_files):
        """创建快照"""
        fm = setup_env["file_manager"]
        mm = setup_env["manifest_manager"]
        
        # 先上传文件
        file_paths = []
        for i, f in enumerate(test_files):
            result = fm.upload_file(f, "test_user", f"D/projects/file{i}.txt")
            file_paths.append(result.path)
        
        # 创建快照
        manifest = mm.create_manifest(
            user_name="test_user",
            file_paths=file_paths
        )
        
        assert manifest.job_id.startswith("job_")
        assert manifest.user_name == "test_user"
        assert manifest.file_count == 3
        assert manifest.status == "submitted"
    
    def test_manifest_with_custom_job_id(self, setup_env, test_files):
        """使用自定义 job_id"""
        fm = setup_env["file_manager"]
        mm = setup_env["manifest_manager"]
        
        # 上传文件
        result = fm.upload_file(test_files[0], "test_user", "D/projects/test.txt")
        
        # 创建快照
        manifest = mm.create_manifest(
            user_name="test_user",
            file_paths=[result.path],
            job_id="custom_job_123"
        )
        
        assert manifest.job_id == "custom_job_123"
    
    def test_get_manifest(self, setup_env, test_files):
        """获取快照信息"""
        fm = setup_env["file_manager"]
        mm = setup_env["manifest_manager"]
        
        result = fm.upload_file(test_files[0], "test_user", "D/projects/test.txt")
        manifest = mm.create_manifest("test_user", [result.path])
        
        # 获取快照
        info = mm.get_manifest(manifest.job_id)
        
        assert info is not None
        assert info.job_id == manifest.job_id
        assert info.file_count == 1
    
    def test_get_manifest_entries(self, setup_env, test_files):
        """获取快照明细"""
        fm = setup_env["file_manager"]
        mm = setup_env["manifest_manager"]
        
        results = []
        for i, f in enumerate(test_files):
            r = fm.upload_file(f, "test_user", f"D/projects/file{i}.txt")
            results.append(r)
        
        manifest = mm.create_manifest(
            "test_user",
            [r.path for r in results]
        )
        
        entries = mm.get_manifest_entries(manifest.job_id)
        
        assert len(entries) == 3
        paths = [e.original_path for e in entries]
        assert "D/projects/file0.txt" in paths
        assert "D/projects/file1.txt" in paths
        assert "D/projects/file2.txt" in paths
    
    def test_update_status(self, setup_env, test_files):
        """更新快照状态"""
        fm = setup_env["file_manager"]
        mm = setup_env["manifest_manager"]
        
        result = fm.upload_file(test_files[0], "test_user", "D/projects/test.txt")
        manifest = mm.create_manifest("test_user", [result.path])
        
        # 更新状态
        mm.update_status(manifest.job_id, "rendering")
        
        info = mm.get_manifest(manifest.job_id)
        assert info.status == "rendering"
    
    def test_list_manifests(self, setup_env, test_files):
        """列出快照"""
        fm = setup_env["file_manager"]
        mm = setup_env["manifest_manager"]
        
        # 创建多个快照
        for i in range(3):
            f = test_files[i % len(test_files)]
            result = fm.upload_file(f, "test_user", f"D/projects/file{i}.txt")
            mm.create_manifest("test_user", [result.path], job_id=f"job_{i}")
        
        manifests = mm.list_manifests(user_name="test_user")
        
        assert len(manifests) == 3
    
    def test_manifest_json_created(self, setup_env, test_files):
        """manifest.json 文件被创建"""
        fm = setup_env["file_manager"]
        mm = setup_env["manifest_manager"]
        jobs_path = setup_env["jobs_path"]
        
        result = fm.upload_file(test_files[0], "test_user", "D/projects/test.txt")
        manifest = mm.create_manifest("test_user", [result.path])
        
        manifest_file = jobs_path / manifest.job_id / "manifest.json"
        
        assert manifest_file.exists()
        
        # 验证内容
        import json
        data = json.loads(manifest_file.read_text())
        assert data["job_id"] == manifest.job_id
        assert data["summary"]["file_count"] == 1  # 修复：使用 summary.file_count
    
    def test_job_directory_created(self, setup_env, test_files):
        """Job 目录结构被创建"""
        fm = setup_env["file_manager"]
        mm = setup_env["manifest_manager"]
        jobs_path = setup_env["jobs_path"]
        
        result = fm.upload_file(test_files[0], "test_user", "D/projects/test.txt")
        manifest = mm.create_manifest("test_user", [result.path])
        
        job_dir = jobs_path / manifest.job_id
        
        assert job_dir.exists()
        # 验证文件链接/副本存在
        file_in_job = job_dir / "D" / "projects" / "test.txt"
        assert file_in_job.exists()


class TestStorageCleaner:
    """测试清理机制"""
    
    @pytest.fixture
    def setup_env(self, tmp_path):
        """创建测试环境"""
        storage_path = tmp_path / "storage"
        users_path = tmp_path / "users"
        jobs_path = tmp_path / "jobs"
        outputs_path = tmp_path / "outputs"
        trash_path = tmp_path / "trash"
        db_path = tmp_path / "test.db"
        cache_path = tmp_path / "hash_cache.json"
        
        blob_storage = BlobStorage(storage_path)
        blob_storage.init_storage()
        
        db = Database(db_path)
        db.init_schema()
        
        hash_cache = HashCache(cache_path)
        
        file_manager = FileManager(db, blob_storage, users_path, hash_cache)
        file_manager.init_directories()
        
        manifest_manager = ManifestManager(db, file_manager, jobs_path, outputs_path)
        manifest_manager.init_directories()
        
        cleaner = StorageCleaner(db, blob_storage, trash_path)
        cleaner.init_trash()
        
        return {
            "cleaner": cleaner,
            "manifest_manager": manifest_manager,
            "file_manager": file_manager,
            "blob_storage": blob_storage,
            "trash_path": trash_path,
            "tmp_path": tmp_path,
        }
    
    @pytest.fixture
    def test_file(self, tmp_path):
        """创建测试文件"""
        f = tmp_path / "test.txt"
        f.write_bytes(b"test content\n")
        return f
    
    def test_ref_count_with_active_file(self, setup_env, test_file):
        """活跃文件有引用"""
        fm = setup_env["file_manager"]
        cleaner = setup_env["cleaner"]
        
        result = fm.upload_file(test_file, "test_user", "D/projects/test.txt")
        
        ref_counts = cleaner.get_ref_counts()
        
        assert result.hash in ref_counts
        assert ref_counts[result.hash].ref_count >= 1
        assert ref_counts[result.hash].user_refs >= 1
    
    def test_ref_count_with_manifest(self, setup_env, test_file):
        """快照增加引用"""
        fm = setup_env["file_manager"]
        mm = setup_env["manifest_manager"]
        cleaner = setup_env["cleaner"]
        
        result = fm.upload_file(test_file, "test_user", "D/projects/test.txt")
        mm.create_manifest("test_user", [result.path])
        
        ref_counts = cleaner.get_ref_counts()
        
        assert ref_counts[result.hash].job_refs >= 1
        assert ref_counts[result.hash].ref_count >= 2
    
    def test_zero_ref_after_delete(self, setup_env, test_file):
        """删除文件后引用计数变化"""
        fm = setup_env["file_manager"]
        cleaner = setup_env["cleaner"]
        db = setup_env["cleaner"].db
        
        result = fm.upload_file(test_file, "test_user", "D/projects/test.txt")
        
        # 标记文件为删除
        db.execute(
            "UPDATE files SET status = 'deleted' WHERE user_name = ? AND path = ?",
            ("test_user", result.path)
        )
        
        ref_counts = cleaner.get_ref_counts()
        
        # 用户引用应该消失
        # 如果哈希不在 ref_counts 中，说明没有任何引用
        if result.hash in ref_counts:
            assert ref_counts[result.hash].user_refs == 0
        else:
            # 哈希完全不在引用中，说明确实是零引用
            pass
    
    def test_move_to_trash(self, setup_env, test_file):
        """移动 blob 到回收站"""
        fm = setup_env["file_manager"]
        cleaner = setup_env["cleaner"]
        blob_storage = setup_env["blob_storage"]
        trash_path = setup_env["trash_path"]
        
        result = fm.upload_file(test_file, "test_user", "D/projects/test.txt")
        
        # 移动到回收站
        success = cleaner.move_to_trash(result.hash)
        
        assert success is True
        assert not blob_storage.exists(result.hash)
        
        # 验证回收站中存在
        trash_file = trash_path / result.hash[:2] / result.hash[2:4] / result.hash
        assert trash_file.exists()
    
    def test_restore_from_trash(self, setup_env, test_file):
        """从回收站恢复"""
        fm = setup_env["file_manager"]
        cleaner = setup_env["cleaner"]
        blob_storage = setup_env["blob_storage"]
        
        result = fm.upload_file(test_file, "test_user", "D/projects/test.txt")
        
        # 移动到回收站
        cleaner.move_to_trash(result.hash)
        
        # 恢复
        success = cleaner.restore_from_trash(result.hash)
        
        assert success is True
        assert blob_storage.exists(result.hash)
    
    def test_cleanup_zero_refs(self, setup_env, test_file):
        """清理零引用 blob"""
        fm = setup_env["file_manager"]
        cleaner = setup_env["cleaner"]
        blob_storage = setup_env["blob_storage"]
        db = setup_env["cleaner"].db
        
        # 上传文件
        result = fm.upload_file(test_file, "test_user", "D/projects/test.txt")
        
        # 验证 blob 存在
        assert blob_storage.exists(result.hash)
        
        # 标记文件为删除（变为零引用）
        db.execute(
            "UPDATE files SET status = 'deleted' WHERE user_name = ? AND path = ?",
            ("test_user", result.path)
        )
        
        # 验证零引用
        zero_refs = cleaner.get_zero_ref_hashes()
        assert result.hash in zero_refs, f"Hash {result.hash[:16]}... should be zero ref"
        
        # 清理
        stats = cleaner.cleanup_zero_refs()
        
        assert stats["moved"] >= 1, f"Expected moved >= 1, got {stats}"
        # blob 应该被移到回收站
        assert not blob_storage.exists(result.hash)
    
    def test_cleanup_dry_run(self, setup_env, test_file):
        """清理演练模式"""
        fm = setup_env["file_manager"]
        cleaner = setup_env["cleaner"]
        blob_storage = setup_env["blob_storage"]
        db = setup_env["cleaner"].db
        
        result = fm.upload_file(test_file, "test_user", "D/projects/test.txt")
        
        # 标记为删除
        db.execute(
            "UPDATE files SET status = 'deleted' WHERE user_name = ? AND path = ?",
            ("test_user", result.path)
        )
        
        # 演练模式
        stats = cleaner.cleanup_zero_refs(dry_run=True)
        
        assert "would_move" in stats
        assert stats["would_move"] >= 1
        # blob 应该还在
        assert blob_storage.exists(result.hash)
    
    def test_empty_trash(self, setup_env, test_file):
        """清空回收站"""
        fm = setup_env["file_manager"]
        cleaner = setup_env["cleaner"]
        cleaner.grace_hours = 0  # 设置为 0 立即过期
        db = setup_env["cleaner"].db
        
        result = fm.upload_file(test_file, "test_user", "D/projects/test.txt")
        
        # 标记为删除并清理
        db.execute(
            "UPDATE files SET status = 'deleted' WHERE user_name = ? AND path = ?",
            ("test_user", result.path)
        )
        cleaner.cleanup_zero_refs()
        
        # 清空回收站
        stats = cleaner.empty_trash(force=True)
        
        assert stats["deleted"] >= 1
    
    def test_get_cleanup_report(self, setup_env, test_file):
        """获取清理报告"""
        fm = setup_env["file_manager"]
        cleaner = setup_env["cleaner"]
        
        fm.upload_file(test_file, "test_user", "D/projects/test.txt")
        
        report = cleaner.get_cleanup_report()
        
        assert "blobs" in report
        assert "trash" in report
        assert report["blobs"]["total_count"] >= 1
