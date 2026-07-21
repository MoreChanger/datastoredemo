"""
单元测试 - 文件管理器

测试内容：
- 文件上传
- 版本管理
- 路径规范化
"""

import os
import tempfile
import pytest
from pathlib import Path

from src.storage import (
    FileManager,
    BlobStorage,
    Database,
    normalize_windows_path,
    UploadResult,
)


class TestNormalizeWindowsPath:
    """测试路径规范化"""
    
    def test_normalize_local_path(self):
        """本地路径规范化"""
        assert normalize_windows_path("D:\\Projects\\file.txt") == "D/projects/file.txt"
        assert normalize_windows_path("d:\\projects\\FILE.TXT") == "D/projects/file.txt"
        assert normalize_windows_path("E:\\test\\path\\file.txt") == "E/test/path/file.txt"
    
    def test_normalize_unc_path(self):
        """UNC 路径规范化"""
        # UNC 路径保留 server/share，路径部分转小写
        result = normalize_windows_path("\\\\server\\share\\Path\\File.txt")
        assert result.startswith("//server/share/")
        assert "path" in result
        assert "file.txt" in result
    
    def test_normalize_forward_slash(self):
        """正斜杠路径"""
        assert normalize_windows_path("D:/Projects/file.txt") == "D/projects/file.txt"
    
    def test_normalize_double_slashes(self):
        """合并连续斜杠"""
        assert normalize_windows_path("D:\\\\Projects\\\\file.txt") == "D/projects/file.txt"


class TestFileManager:
    """测试文件管理器"""
    
    @pytest.fixture
    def setup_storage(self, tmp_path):
        """创建完整的测试环境"""
        storage_path = tmp_path / "storage"
        users_path = tmp_path / "users"
        db_path = tmp_path / "test.db"
        
        blob_storage = BlobStorage(storage_path)
        blob_storage.init_storage()
        
        db = Database(db_path)
        db.init_schema()
        
        file_manager = FileManager(db, blob_storage, users_path)
        file_manager.init_directories()
        
        return {
            "file_manager": file_manager,
            "blob_storage": blob_storage,
            "db": db,
            "tmp_path": tmp_path,
        }
    
    @pytest.fixture
    def test_file(self, tmp_path):
        """创建测试文件"""
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"test content\n")
        return test_file
    
    def test_upload_new_file(self, setup_storage, test_file):
        """上传新文件"""
        fm = setup_storage["file_manager"]
        
        result = fm.upload_file(
            local_path=test_file,
            user_name="test_user",
            server_path="D/projects/test.txt"
        )
        
        assert isinstance(result, UploadResult)
        assert result.path == "D/projects/test.txt"
        assert result.version == 1
        assert result.created is True
        assert len(result.hash) == 64
        assert result.size == test_file.stat().st_size
    
    def test_upload_same_content_no_new_version(self, setup_storage, test_file):
        """相同内容不上传新版本"""
        fm = setup_storage["file_manager"]
        
        # 第一次上传
        result1 = fm.upload_file(
            local_path=test_file,
            user_name="test_user",
            server_path="D/projects/test.txt"
        )
        
        # 第二次上传相同内容
        result2 = fm.upload_file(
            local_path=test_file,
            user_name="test_user",
            server_path="D/projects/test.txt"
        )
        
        assert result1.version == 1
        assert result2.version == 1  # 版本号不变
        assert result2.created is False  # 未创建新版本
        assert result1.hash == result2.hash
    
    def test_upload_different_content_new_version(self, setup_storage, tmp_path):
        """不同内容创建新版本"""
        fm = setup_storage["file_manager"]
        
        # 第一版
        file_v1 = tmp_path / "v1.txt"
        file_v1.write_bytes(b"version 1\n")
        result1 = fm.upload_file(
            local_path=file_v1,
            user_name="test_user",
            server_path="D/projects/test.txt"
        )
        
        # 第二版（相同路径，不同内容）
        file_v2 = tmp_path / "v2.txt"
        file_v2.write_bytes(b"version 2\n")
        result2 = fm.upload_file(
            local_path=file_v2,
            user_name="test_user",
            server_path="D/projects/test.txt"
        )
        
        assert result1.version == 1
        assert result2.version == 2  # 版本号递增
        assert result2.created is True
        assert result1.hash != result2.hash  # 哈希不同
    
    def test_upload_deduplication(self, setup_storage, tmp_path):
        """不同路径相同内容去重"""
        fm = setup_storage["file_manager"]
        
        # 相同内容
        content = b"duplicate content\n"
        file1 = tmp_path / "file1.txt"
        file1.write_bytes(content)
        file2 = tmp_path / "file2.txt"
        file2.write_bytes(content)
        
        result1 = fm.upload_file(file1, "user1", "D/projects/file1.txt")
        result2 = fm.upload_file(file2, "user1", "D/projects/file2.txt")
        
        # 哈希相同（去重）
        assert result1.hash == result2.hash
        # 第一个文件创建了新 blob
        assert result1.deduplicated is False
        # 第二个文件复用了已有 blob
        assert result2.deduplicated is True
    
    def test_multi_user_isolation(self, setup_storage, test_file):
        """多用户隔离"""
        fm = setup_storage["file_manager"]
        
        # 用户1上传
        result1 = fm.upload_file(
            test_file, "user1", "D/projects/test.txt"
        )
        
        # 用户2上传相同路径
        result2 = fm.upload_file(
            test_file, "user2", "D/projects/test.txt"
        )
        
        # 应该是两个独立的文件记录
        info1 = fm.get_file_info("user1", "D/projects/test.txt")
        info2 = fm.get_file_info("user2", "D/projects/test.txt")
        
        assert info1.file_id != info2.file_id
    
    def test_get_file_info(self, setup_storage, test_file):
        """获取文件信息"""
        fm = setup_storage["file_manager"]
        
        fm.upload_file(test_file, "test_user", "D/projects/test.txt")
        
        info = fm.get_file_info("test_user", "D/projects/test.txt")
        
        assert info is not None
        assert info.path == "D/projects/test.txt"
        assert info.status == "active"
        assert info.current_version == 1
    
    def test_get_version_history(self, setup_storage, tmp_path):
        """获取版本历史"""
        fm = setup_storage["file_manager"]
        
        # 上传多个版本
        for i in range(3):
            file_v = tmp_path / f"v{i}.txt"
            file_v.write_bytes(f"version {i}\n".encode())
            fm.upload_file(file_v, "test_user", "D/projects/test.txt")
        
        history = fm.get_version_history("test_user", "D/projects/test.txt")
        
        assert len(history) == 3
        assert history[0]["version_num"] == 3  # 降序排列
        assert history[2]["version_num"] == 1
    
    def test_list_user_files(self, setup_storage, tmp_path):
        """列出用户文件"""
        fm = setup_storage["file_manager"]
        
        # 上传多个文件
        for i in range(3):
            file_v = tmp_path / f"file{i}.txt"
            file_v.write_bytes(f"content {i}\n".encode())
            fm.upload_file(file_v, "test_user", f"D/projects/file{i}.txt")
        
        files = fm.list_user_files("test_user")
        
        assert len(files) == 3
        paths = [f["path"] for f in files]
        assert "D/projects/file0.txt" in paths
        assert "D/projects/file1.txt" in paths
        assert "D/projects/file2.txt" in paths
    
    def test_get_storage_stats(self, setup_storage, tmp_path):
        """获取存储统计"""
        fm = setup_storage["file_manager"]
        
        # 上传多个文件
        for i in range(3):
            file_v = tmp_path / f"file{i}.txt"
            file_v.write_bytes(f"content {i}\n".encode())
            fm.upload_file(file_v, "user1", f"D/projects/file{i}.txt")
        
        stats = fm.get_storage_stats()
        
        assert stats["file_count"] == 3
        assert stats["blob_count"] == 3  # 3个不同内容
        assert stats["version_count"] == 3
        assert stats["total_size_bytes"] > 0
