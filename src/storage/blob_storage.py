"""
Blob Storage - 内容寻址存储模块

实现 SHA-256 内容寻址存储，支持：
- 全局去重（相同内容只存一份）
- 原子写入（临时文件 -> 校验 -> 重命名）
- 完整性验证（写入后校验哈希）
"""

import hashlib
import os
import shutil
from pathlib import Path
from typing import Optional, Tuple


def compute_sha256(file_path: str | Path, chunk_size: int = 65536) -> str:
    """
    计算文件的 SHA-256 哈希值
    
    Args:
        file_path: 文件路径
        chunk_size: 分块大小（默认 64KB，适合大文件）
    
    Returns:
        64 字符的十六进制哈希字符串
    """
    sha256_hash = hashlib.sha256()
    
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(chunk_size), b''):
            sha256_hash.update(chunk)
    
    return sha256_hash.hexdigest()


def get_blob_path(base_path: str | Path, file_hash: str) -> Path:
    """
    根据哈希值计算 blob 存储路径
    
    路径格式: /blobs/{hash[:2]}/{hash[2:4]}/{hash}
    
    Args:
        base_path: 存储根目录
        file_hash: SHA-256 哈希值（64 字符）
    
    Returns:
        blob 文件的完整路径
    """
    if len(file_hash) != 64:
        raise ValueError(f"Invalid SHA-256 hash length: {len(file_hash)}, expected 64")
    
    if not all(c in '0123456789abcdef' for c in file_hash):
        raise ValueError(f"Invalid SHA-256 hash format: must be lowercase hex")
    
    return Path(base_path) / "blobs" / file_hash[:2] / file_hash[2:4] / file_hash


class BlobStorage:
    """
    本地 Blob 存储实现
    
    功能：
    - 内容寻址存储（SHA-256）
    - 自动去重
    - 原子写入
    - 完整性验证
    """
    
    def __init__(self, base_path: str | Path):
        """
        初始化 Blob 存储
        
        Args:
            base_path: 存储根目录（如 /data/storage）
        """
        self.base_path = Path(base_path)
        self.blobs_path = self.base_path / "blobs"
        self.uploading_path = self.blobs_path / ".uploading"
    
    def init_storage(self) -> None:
        """初始化存储目录结构"""
        self.blobs_path.mkdir(parents=True, exist_ok=True)
        self.uploading_path.mkdir(parents=True, exist_ok=True)
    
    def exists(self, file_hash: str) -> bool:
        """
        检查 blob 是否已存在
        
        Args:
            file_hash: SHA-256 哈希值
        
        Returns:
            是否存在
        """
        blob_path = get_blob_path(self.base_path, file_hash)
        return blob_path.exists()
    
    def get_path(self, file_hash: str) -> Optional[Path]:
        """
        获取 blob 文件路径
        
        Args:
            file_hash: SHA-256 哈希值
        
        Returns:
            blob 路径（不存在则返回 None）
        """
        blob_path = get_blob_path(self.base_path, file_hash)
        return blob_path if blob_path.exists() else None
    
    def store(self, source_path: str | Path, file_hash: Optional[str] = None) -> Tuple[Path, bool]:
        """
        存储文件到 blob 存储
        
        流程：
        1. 计算哈希（如未提供）
        2. 检查是否已存在（去重）
        3. 写入临时文件
        4. 校验完整性
        5. 原子重命名
        
        Args:
            source_path: 源文件路径
            file_hash: 预计算的哈希值（可选，不提供则自动计算）
        
        Returns:
            (blob 路径, 是否新创建)
        
        Raises:
            FileNotFoundError: 源文件不存在
            ValueError: 哈希校验失败
        """
        source_path = Path(source_path)
        
        if not source_path.exists():
            raise FileNotFoundError(f"Source file not found: {source_path}")
        
        # 1. 计算哈希（如未提供）
        if file_hash is None:
            file_hash = compute_sha256(source_path)
        
        blob_path = get_blob_path(self.base_path, file_hash)
        
        # 2. 检查是否已存在（去重）
        if blob_path.exists():
            return blob_path, False  # 已存在，去重成功
        
        # 3. 准备写入
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.uploading_path / f"{file_hash}.uploading"
        
        try:
            # 4. 写入临时文件
            shutil.copy2(source_path, temp_path)
            
            # 5. 校验完整性（重新计算哈希）
            actual_hash = compute_sha256(temp_path)
            if actual_hash != file_hash:
                temp_path.unlink(missing_ok=True)
                raise ValueError(
                    f"Hash mismatch after write: expected {file_hash[:16]}..., "
                    f"got {actual_hash[:16]}..."
                )
            
            # 6. 原子重命名
            temp_path.rename(blob_path)
            
            return blob_path, True  # 新创建成功
            
        except Exception as e:
            # 清理临时文件
            temp_path.unlink(missing_ok=True)
            raise
    
    def store_content(self, content: bytes, file_hash: Optional[str] = None) -> Tuple[Path, bool]:
        """
        直接存储二进制内容
        
        Args:
            content: 文件内容（字节）
            file_hash: 预计算的哈希值（可选）
        
        Returns:
            (blob 路径, 是否新创建)
        """
        # 计算哈希
        if file_hash is None:
            file_hash = hashlib.sha256(content).hexdigest()
        
        blob_path = get_blob_path(self.base_path, file_hash)
        
        # 检查是否已存在
        if blob_path.exists():
            return blob_path, False
        
        # 准备写入
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.uploading_path / f"{file_hash}.uploading"
        
        try:
            # 写入临时文件
            temp_path.write_bytes(content)
            
            # 校验完整性
            actual_hash = compute_sha256(temp_path)
            if actual_hash != file_hash:
                temp_path.unlink(missing_ok=True)
                raise ValueError(f"Hash mismatch: expected {file_hash[:16]}..., got {actual_hash[:16]}...")
            
            # 原子重命名
            temp_path.rename(blob_path)
            
            return blob_path, True
            
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
    
    def get_stats(self) -> dict:
        """
        获取存储统计信息
        
        Returns:
            包含 blob 数量、总大小等统计信息的字典
        """
        total_count = 0
        total_size = 0
        
        for hash_dir in self.blobs_path.iterdir():
            if hash_dir.name == ".uploading":
                continue
            if not hash_dir.is_dir():
                continue
            
            for sub_dir in hash_dir.iterdir():
                if not sub_dir.is_dir():
                    continue
                
                for blob_file in sub_dir.iterdir():
                    if blob_file.is_file() and len(blob_file.name) == 64:
                        total_count += 1
                        total_size += blob_file.stat().st_size
        
        return {
            "blob_count": total_count,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
        }
