"""
单元测试 - 哈希缓存

测试内容：
- 缓存命中/未命中
- 缓存失效检测
- 抽样验证
- 缓存持久化
"""

import os
import time
import tempfile
import pytest
from pathlib import Path

from src.storage import HashCache, compute_sha256


class TestHashCache:
    """测试哈希缓存"""
    
    @pytest.fixture
    def cache(self, tmp_path):
        """创建临时缓存"""
        cache_path = tmp_path / "hash_cache.json"
        return HashCache(cache_path)
    
    @pytest.fixture
    def test_file(self, tmp_path):
        """创建测试文件"""
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"test content\n")
        return test_file
    
    def test_cache_miss(self, cache, test_file):
        """缓存未命中"""
        mtime = test_file.stat().st_mtime
        size = test_file.stat().st_size
        
        result = cache.get("D/projects/test.txt", mtime, size)
        
        assert result is None
    
    def test_cache_hit(self, cache, test_file):
        """缓存命中"""
        mtime = test_file.stat().st_mtime
        size = test_file.stat().st_size
        file_hash = compute_sha256(test_file)
        
        # 设置缓存
        cache.set("D/projects/test.txt", mtime, size, file_hash)
        
        # 读取缓存
        result = cache.get("D/projects/test.txt", mtime, size)
        
        assert result == file_hash
    
    def test_cache_invalidation_mtime(self, cache, test_file):
        """缓存失效：mtime 变化"""
        mtime = test_file.stat().st_mtime
        size = test_file.stat().st_size
        file_hash = compute_sha256(test_file)
        
        cache.set("D/projects/test.txt", mtime, size, file_hash)
        
        # mtime 变化
        new_mtime = mtime + 1
        result = cache.get("D/projects/test.txt", new_mtime, size)
        
        assert result is None
    
    def test_cache_invalidation_size(self, cache, test_file):
        """缓存失效：size 变化"""
        mtime = test_file.stat().st_mtime
        size = test_file.stat().st_size
        file_hash = compute_sha256(test_file)
        
        cache.set("D/projects/test.txt", mtime, size, file_hash)
        
        # size 变化
        new_size = size + 1
        result = cache.get("D/projects/test.txt", mtime, new_size)
        
        assert result is None
    
    def test_cache_persistence(self, tmp_path, test_file):
        """缓存持久化"""
        cache_path = tmp_path / "hash_cache.json"
        
        mtime = test_file.stat().st_mtime
        size = test_file.stat().st_size
        file_hash = compute_sha256(test_file)
        
        # 创建缓存并保存
        cache1 = HashCache(cache_path)
        cache1.set("D/projects/test.txt", mtime, size, file_hash)
        cache1.save()
        
        # 验证文件存在
        assert cache_path.exists()
        
        # 加载缓存
        cache2 = HashCache(cache_path)
        result = cache2.get("D/projects/test.txt", mtime, size)
        
        assert result == file_hash
    
    def test_cache_clear(self, cache, test_file):
        """清空缓存"""
        mtime = test_file.stat().st_mtime
        size = test_file.stat().st_size
        file_hash = compute_sha256(test_file)
        
        cache.set("D/projects/test.txt", mtime, size, file_hash)
        cache.clear()
        
        result = cache.get("D/projects/test.txt", mtime, size)
        
        assert result is None
    
    def test_cache_invalidate_path(self, cache, test_file):
        """使指定路径缓存失效"""
        mtime = test_file.stat().st_mtime
        size = test_file.stat().st_size
        file_hash = compute_sha256(test_file)
        
        cache.set("D/projects/test.txt", mtime, size, file_hash)
        cache.invalidate("D/projects/test.txt")
        
        result = cache.get("D/projects/test.txt", mtime, size)
        
        assert result is None
    
    def test_cache_stats(self, cache, test_file):
        """缓存统计"""
        mtime = test_file.stat().st_mtime
        size = test_file.stat().st_size
        file_hash = compute_sha256(test_file)
        
        stats = cache.get_stats()
        assert stats["entry_count"] == 0
        
        cache.set("D/projects/test.txt", mtime, size, file_hash)
        
        stats = cache.get_stats()
        assert stats["entry_count"] == 1
    
    def test_cache_prune(self, cache, test_file):
        """清理过期缓存"""
        mtime = test_file.stat().st_mtime
        size = test_file.stat().st_size
        file_hash = compute_sha256(test_file)
        
        # 添加新条目
        cache.set("D/projects/new.txt", mtime, size, file_hash)
        
        # 手动修改缓存时间为 31 天前
        import time
        old_time = time.time() - (31 * 24 * 60 * 60)
        cache._cache["D/projects/old.txt"] = cache._cache["D/projects/new.txt"].__class__(
            mtime=mtime,
            size=size,
            hash=file_hash,
            cached_at=old_time
        )
        cache.save()
        
        # 清理超过 30 天的缓存
        removed = cache.prune(max_age_days=30)
        
        assert removed == 1
        assert "D/projects/old.txt" not in cache._cache
        assert "D/projects/new.txt" in cache._cache
    
    def test_cache_corrupted_file(self, tmp_path):
        """缓存文件损坏时重建"""
        cache_path = tmp_path / "hash_cache.json"
        
        # 写入无效 JSON
        cache_path.write_text("invalid json {")
        
        # 加载时应该忽略损坏的文件
        cache = HashCache(cache_path)
        stats = cache.get_stats()
        
        assert stats["entry_count"] == 0
