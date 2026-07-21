"""
单元测试 - Blob 存储

测试内容：
- SHA-256 哈希计算
- Blob 存储与去重
- 原子写入
- 完整性验证
"""

import os
import tempfile
import shutil
import pytest
from pathlib import Path

from src.storage import BlobStorage, compute_sha256, get_blob_path


class TestComputeSHA256:
    """测试 SHA-256 哈希计算"""
    
    def test_compute_sha256_empty_file(self, tmp_path):
        """空文件的哈希值"""
        empty_file = tmp_path / "empty.txt"
        empty_file.write_bytes(b"")
        
        # SHA-256 of empty string
        expected = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert compute_sha256(empty_file) == expected
    
    def test_compute_sha256_known_content(self, tmp_path):
        """已知内容的哈希值"""
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"hello world\n")
        
        # SHA-256 of "hello world\n"
        expected = "a948904f2f0f479b8f8197694b30184b0d2ed1c1cd2a1ec0fb85d299a192a447"
        assert compute_sha256(test_file) == expected
    
    def test_compute_sha256_large_file(self, tmp_path):
        """大文件哈希计算（分块读取）"""
        large_file = tmp_path / "large.bin"
        # 创建 1MB 文件
        large_file.write_bytes(os.urandom(1024 * 1024))
        
        # 应该能正常计算
        hash1 = compute_sha256(large_file)
        assert len(hash1) == 64
        assert all(c in '0123456789abcdef' for c in hash1)


class TestGetBlobPath:
    """测试 blob 路径生成"""
    
    def test_get_blob_path_format(self):
        """路径格式正确"""
        hash_value = "a" * 64
        path = get_blob_path("/storage", hash_value)
        
        expected = Path("/storage/blobs/aa/aa/" + "a" * 64)
        assert path == expected
    
    def test_get_blob_path_invalid_length(self):
        """无效哈希长度"""
        with pytest.raises(ValueError, match="Invalid SHA-256 hash length"):
            get_blob_path("/storage", "abc123")
    
    def test_get_blob_path_invalid_format(self):
        """无效哈希格式"""
        with pytest.raises(ValueError, match="Invalid SHA-256 hash format"):
            get_blob_path("/storage", "A" * 64)  # 大写字母


class TestBlobStorage:
    """测试 Blob 存储"""
    
    @pytest.fixture
    def storage(self, tmp_path):
        """创建临时存储"""
        storage_path = tmp_path / "storage"
        storage = BlobStorage(storage_path)
        storage.init_storage()
        return storage
    
    @pytest.fixture
    def test_file(self, tmp_path):
        """创建测试文件"""
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"test content\n")
        return test_file
    
    def test_init_storage(self, tmp_path):
        """初始化存储目录"""
        storage_path = tmp_path / "storage"
        storage = BlobStorage(storage_path)
        storage.init_storage()
        
        assert storage_path.exists()
        assert (storage_path / "blobs").exists()
        assert (storage_path / "blobs" / ".uploading").exists()
    
    def test_store_new_file(self, storage, test_file):
        """存储新文件"""
        blob_path, created = storage.store(test_file)
        
        assert created is True
        assert blob_path.exists()
        assert blob_path.stat().st_size == test_file.stat().st_size
    
    def test_store_deduplication(self, storage, test_file, tmp_path):
        """去重功能"""
        # 第一次存储
        blob_path1, created1 = storage.store(test_file)
        
        # 第二次存储相同内容（不同文件名）
        test_file2 = tmp_path / "test2.txt"
        test_file2.write_bytes(b"test content\n")  # 相同内容
        blob_path2, created2 = storage.store(test_file2)
        
        # 应该去重
        assert created1 is True
        assert created2 is False  # 去重
        assert blob_path1 == blob_path2  # 指向同一个 blob
    
    def test_store_with_precomputed_hash(self, storage, test_file):
        """使用预计算的哈希值"""
        file_hash = compute_sha256(test_file)
        blob_path, created = storage.store(test_file, file_hash)
        
        assert created is True
        # 验证路径正确
        expected_path = get_blob_path(storage.base_path, file_hash)
        assert blob_path == expected_path
    
    def test_store_hash_mismatch_detection(self, storage, test_file):
        """哈希不匹配检测（模拟写入后内容损坏）"""
        # 使用错误的哈希值
        wrong_hash = "b" * 64
        
        with pytest.raises(ValueError, match="Hash mismatch"):
            storage.store(test_file, wrong_hash)
    
    def test_exists(self, storage, test_file):
        """检查 blob 是否存在"""
        file_hash = compute_sha256(test_file)
        
        assert storage.exists(file_hash) is False
        
        storage.store(test_file)
        
        assert storage.exists(file_hash) is True
    
    def test_get_path(self, storage, test_file):
        """获取 blob 路径"""
        file_hash = compute_sha256(test_file)
        
        # 不存在时返回 None
        assert storage.get_path(file_hash) is None
        
        # 存储后返回路径
        blob_path, _ = storage.store(test_file)
        result = storage.get_path(file_hash)
        
        assert result == blob_path
    
    def test_store_content(self, storage):
        """直接存储二进制内容"""
        content = b"direct content\n"
        blob_path, created = storage.store_content(content)
        
        assert created is True
        assert blob_path.exists()
        assert blob_path.read_bytes() == content
    
    def test_get_stats(self, storage, test_file):
        """获取存储统计"""
        # 空存储
        stats = storage.get_stats()
        assert stats["blob_count"] == 0
        assert stats["total_size_bytes"] == 0
        
        # 存储文件
        storage.store(test_file)
        
        stats = storage.get_stats()
        assert stats["blob_count"] == 1
        assert stats["total_size_bytes"] == test_file.stat().st_size
    
    def test_atomic_write_rollback(self, storage, test_file, monkeypatch):
        """原子写入失败时回滚"""
        # 模拟重命名失败
        import os
        original_rename = os.rename
        
        def failing_rename(src, dst):
            if ".uploading" in str(src):
                raise OSError("Simulated rename failure")
            return original_rename(src, dst)
        
        monkeypatch.setattr(os, "rename", failing_rename)
        
        with pytest.raises(OSError):
            storage.store(test_file)
        
        # 临时文件应该被清理
        uploading_dir = storage.uploading_path
        assert len(list(uploading_dir.iterdir())) == 0
