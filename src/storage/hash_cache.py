"""
哈希缓存模块 - mtime + size 优化

避免重复计算未修改文件的 SHA-256 哈希。

缓存策略：
- 本地缓存：存储在用户目录下的 JSON 文件
- 缓存键：规范化后的路径
- 缓存值：{mtime, size, hash}
- 失效条件：mtime 或 size 变化
"""

import json
import os
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict
import random


@dataclass
class CacheEntry:
    """缓存条目"""
    mtime: float      # 文件修改时间
    size: int         # 文件大小
    hash: str         # SHA-256 哈希值
    cached_at: float  # 缓存时间


class HashCache:
    """
    本地哈希缓存管理器
    
    功能：
    - 存储/读取缓存
    - 自动失效检测
    - 1% 抽样验证（检测缓存损坏）
    """
    
    # 抽样验证率：1% 的概率即使缓存命中也重新计算哈希
    SAMPLE_VERIFY_RATE = 0.01
    
    def __init__(self, cache_path: str | Path):
        """
        初始化缓存管理器
        
        Args:
            cache_path: 缓存文件路径（JSON）
        """
        self.cache_path = Path(cache_path)
        self._cache: Dict[str, CacheEntry] = {}
        self._loaded = False
    
    def _ensure_loaded(self) -> None:
        """确保缓存已加载"""
        if not self._loaded:
            self.load()
            self._loaded = True
    
    def load(self) -> None:
        """从文件加载缓存"""
        if self.cache_path.exists():
            try:
                with open(self.cache_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._cache = {
                    k: CacheEntry(**v) for k, v in data.items()
                }
            except (json.JSONDecodeError, KeyError, TypeError):
                # 缓存损坏，重新开始
                self._cache = {}
        else:
            self._cache = {}
    
    def save(self) -> None:
        """保存缓存到文件"""
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        
        data = {k: asdict(v) for k, v in self._cache.items()}
        
        # 原子写入：先写临时文件，再重命名
        temp_path = self.cache_path.with_suffix('.tmp')
        try:
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            temp_path.replace(self.cache_path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
    
    def get(
        self,
        path: str,
        mtime: float,
        size: int
    ) -> Optional[str]:
        """
        尝试从缓存获取哈希值
        
        Args:
            path: 规范化后的文件路径
            mtime: 当前文件修改时间
            size: 当前文件大小
        
        Returns:
            哈希值（缓存命中且有效），None（缓存未命中或失效）
        """
        self._ensure_loaded()
        
        if path not in self._cache:
            return None
        
        entry = self._cache[path]
        
        # 检查 mtime 和 size 是否匹配
        if entry.mtime == mtime and entry.size == size:
            # 缓存命中！
            # 抽样验证：1% 概率强制重新计算
            if random.random() < self.SAMPLE_VERIFY_RATE:
                return None  # 强制重新计算，由调用方验证
            return entry.hash
        
        return None
    
    def set(
        self,
        path: str,
        mtime: float,
        size: int,
        hash: str
    ) -> None:
        """
        设置缓存条目
        
        Args:
            path: 规范化后的文件路径
            mtime: 文件修改时间
            size: 文件大小
            hash: SHA-256 哈希值
        """
        self._ensure_loaded()
        
        import time
        self._cache[path] = CacheEntry(
            mtime=mtime,
            size=size,
            hash=hash,
            cached_at=time.time()
        )
    
    def invalidate(self, path: str) -> None:
        """使指定路径的缓存失效"""
        self._ensure_loaded()
        self._cache.pop(path, None)
    
    def clear(self) -> None:
        """清空缓存"""
        self._cache = {}
        if self.cache_path.exists():
            self.cache_path.unlink()
    
    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计"""
        self._ensure_loaded()
        
        import time
        now = time.time()
        
        # 计算缓存年龄分布
        ages = []
        for entry in self._cache.values():
            ages.append(now - entry.cached_at)
        
        return {
            "entry_count": len(self._cache),
            "cache_file": str(self.cache_path),
            "oldest_entry_age": max(ages) if ages else 0,
            "newest_entry_age": min(ages) if ages else 0,
        }
    
    def prune(self, max_age_days: int = 30) -> int:
        """
        清理过期缓存
        
        Args:
            max_age_days: 最大缓存天数
        
        Returns:
            清理的条目数量
        """
        self._ensure_loaded()
        
        import time
        max_age_seconds = max_age_days * 24 * 60 * 60
        now = time.time()
        
        to_remove = []
        for path, entry in self._cache.items():
            if now - entry.cached_at > max_age_seconds:
                to_remove.append(path)
        
        for path in to_remove:
            del self._cache[path]
        
        if to_remove:
            self.save()
        
        return len(to_remove)
