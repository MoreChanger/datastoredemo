"""
单元测试 - P1 功能集成

测试内容：
- FileManager 与 HashCache 集成
- 用户目录链接管理
"""

import os
import time
import platform
import tempfile
import pytest
from pathlib import Path

from src.storage import (
    FileManager,
    BlobStorage,
    Database,
    HashCache,
)


class TestFileManagerWithCache:
    """测试 FileManager 与缓存集成"""
    
    @pytest.fixture
    def setup_with_cache(self, tmp_path):
        """创建带缓存的测试环境"""
        storage_path = tmp_path / "storage"
        users_path = tmp_path / "users"
        db_path = tmp_path / "test.db"
        cache_path = tmp_path / "hash_cache.json"
        
        blob_storage = BlobStorage(storage_path)
        blob_storage.init_storage()
        
        db = Database(db_path)
        db.init_schema()
        
        hash_cache = HashCache(cache_path)
        
        file_manager = FileManager(db, blob_storage, users_path, hash_cache)
        file_manager.init_directories()
        
        return {
            "file_manager": file_manager,
            "blob_storage": blob_storage,
            "db": db,
            "hash_cache": hash_cache,
            "tmp_path": tmp_path,
        }
    
    @pytest.fixture
    def test_file(self, tmp_path):
        """创建测试文件"""
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"test content\n")
        return test_file
    
    def test_upload_with_cache_miss(self, setup_with_cache, test_file):
        """首次上传：缓存未命中"""
        fm = setup_with_cache["file_manager"]
        cache = setup_with_cache["hash_cache"]
        
        result = fm.upload_file(test_file, "test_user", "D/projects/test.txt")
        
        assert result.cache_hit is False
        assert result.hash is not None
        
        # 缓存应该被设置
        stats = cache.get_stats()
        assert stats["entry_count"] == 1
    
    def test_upload_with_cache_hit(self, setup_with_cache, test_file):
        """再次上传相同文件：缓存命中"""
        fm = setup_with_cache["file_manager"]
        
        # 首次上传
        result1 = fm.upload_file(test_file, "test_user", "D/projects/test.txt")
        assert result1.cache_hit is False
        
        # 等待一小段时间确保 mtime 不会变化
        time.sleep(0.1)
        
        # 再次上传（文件未修改）
        result2 = fm.upload_file(test_file, "test_user", "D/projects/test.txt")
        
        assert result2.cache_hit is True
        assert result2.hash == result1.hash
    
    def test_upload_modified_file_cache_miss(self, setup_with_cache, test_file):
        """文件修改后：缓存失效"""
        fm = setup_with_cache["file_manager"]
        
        # 首次上传
        result1 = fm.upload_file(test_file, "test_user", "D/projects/test.txt")
        assert result1.cache_hit is False
        
        # 修改文件
        time.sleep(0.1)  # 确保 mtime 变化
        test_file.write_bytes(b"modified content\n")
        
        # 再次上传
        result2 = fm.upload_file(test_file, "test_user", "D/projects/test.txt")
        
        assert result2.cache_hit is False  # 缓存未命中
        assert result2.hash != result1.hash  # 哈希不同
    
    def test_upload_without_cache(self, tmp_path, test_file):
        """不使用缓存时：总是计算哈希"""
        storage_path = tmp_path / "storage"
        users_path = tmp_path / "users"
        db_path = tmp_path / "test.db"
        
        blob_storage = BlobStorage(storage_path)
        blob_storage.init_storage()
        
        db = Database(db_path)
        db.init_schema()
        
        # 不传入 hash_cache
        file_manager = FileManager(db, blob_storage, users_path, hash_cache=None)
        file_manager.init_directories()
        
        result = file_manager.upload_file(test_file, "test_user", "D/projects/test.txt")
        
        # cache_hit 应该是 False（因为没有缓存）
        assert result.cache_hit is False


class TestUserDirectoryLink:
    """测试用户目录链接管理"""
    
    @pytest.fixture
    def setup_storage(self, tmp_path):
        """创建测试环境"""
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
            "users_path": users_path,
            "tmp_path": tmp_path,
        }
    
    @pytest.fixture
    def test_file(self, tmp_path):
        """创建测试文件"""
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"test content\n")
        return test_file
    
    def test_user_file_created(self, setup_storage, test_file):
        """用户目录文件被创建"""
        fm = setup_storage["file_manager"]
        users_path = setup_storage["users_path"]
        
        fm.upload_file(test_file, "test_user", "D/projects/test.txt")
        
        user_file = users_path / "test_user" / "D" / "projects" / "test.txt"
        
        assert user_file.exists()
        assert user_file.read_bytes() == test_file.read_bytes()
    
    def test_user_file_updated(self, setup_storage, tmp_path):
        """用户目录文件被更新"""
        fm = setup_storage["file_manager"]
        users_path = setup_storage["users_path"]
        
        # 第一版
        file_v1 = tmp_path / "v1.txt"
        file_v1.write_bytes(b"version 1\n")
        fm.upload_file(file_v1, "test_user", "D/projects/test.txt")
        
        # 第二版
        time.sleep(0.1)
        file_v2 = tmp_path / "v2.txt"
        file_v2.write_bytes(b"version 2\n")
        fm.upload_file(file_v2, "test_user", "D/projects/test.txt")
        
        user_file = users_path / "test_user" / "D" / "projects" / "test.txt"
        
        assert user_file.exists()
        assert user_file.read_bytes() == b"version 2\n"
    
    def test_user_file_is_link_or_copy(self, setup_storage, test_file):
        """用户文件是链接或副本"""
        fm = setup_storage["file_manager"]
        users_path = setup_storage["users_path"]
        
        fm.upload_file(test_file, "test_user", "D/projects/test.txt")
        
        user_file = users_path / "test_user" / "D" / "projects" / "test.txt"
        
        # 在 Windows 上可能是：软链接、硬链接、或副本
        # 在 Linux/macOS 上应该是软链接
        system = platform.system()
        
        if system == "Windows":
            # Windows: 检查是否为硬链接、软链接或普通文件
            # 硬链接和软链接都是有效的
            assert user_file.exists()
        else:
            # Linux/macOS: 检查是否为软链接
            assert user_file.is_symlink()
    
    def test_multiple_users_isolated(self, setup_storage, test_file):
        """多用户隔离"""
        fm = setup_storage["file_manager"]
        users_path = setup_storage["users_path"]
        
        fm.upload_file(test_file, "user1", "D/projects/test.txt")
        fm.upload_file(test_file, "user2", "D/projects/test.txt")
        
        user1_file = users_path / "user1" / "D" / "projects" / "test.txt"
        user2_file = users_path / "user2" / "D" / "projects" / "test.txt"
        
        assert user1_file.exists()
        assert user2_file.exists()
