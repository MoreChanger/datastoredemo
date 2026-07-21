"""
数据库模块 - 元数据存储

使用 SQLite 存储：
- 文件路径与版本映射
- 版本历史
- Manifest 快照（P2 功能）

Schema 设计与设计文档对齐，便于后续迁移到 PostgreSQL
"""

import sqlite3
from pathlib import Path
from typing import Optional
from contextlib import contextmanager


SCHEMA_SQL = """
-- 文件表：记录所有出现过的文件路径
CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_name   TEXT NOT NULL,        -- 文件所属用户（多租户隔离）
    path        TEXT NOT NULL,        -- 规范化后的完整路径
    project     TEXT,                 -- 所属项目（可选）
    status      TEXT DEFAULT 'active'
                CHECK (status IN ('active', 'orphaned', 'deleted')),
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT,
    UNIQUE(user_name, path)           -- 同一用户下路径唯一
);

-- 文件版本表：每次上传内容不同就是新版本
CREATE TABLE IF NOT EXISTS file_versions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id       INTEGER NOT NULL REFERENCES files(id),
    version_num   INTEGER NOT NULL,   -- 版本号，自增：1, 2, 3 ...
    hash          TEXT NOT NULL,      -- SHA-256 哈希值（64 字符）
    size          INTEGER,            -- 文件大小（字节）
    uploaded_by   TEXT,               -- 上传用户
    uploaded_at   TEXT DEFAULT (datetime('now')),
    UNIQUE(file_id, version_num)
);

-- 快照表（Manifest）：每次提交任务的完整文件清单
CREATE TABLE IF NOT EXISTS manifests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id        TEXT NOT NULL UNIQUE,  -- 渲染任务 ID
    user_name     TEXT,                  -- 提交用户
    scene_path    TEXT,                  -- 主场景文件路径
    output_path   TEXT,                  -- 渲染输出目录路径
    drives        TEXT,                  -- 涉及的盘符列表（JSON 数组）
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT,
    archived_at   TEXT,
    status        TEXT DEFAULT 'submitted'
                  CHECK (status IN ('submitted', 'rendering',
                                    'completed', 'failed',
                                    'archiving', 'archived', 'deleted'))
);

-- 快照明细表：记录该次快照中每个文件使用的是哪个版本
CREATE TABLE IF NOT EXISTS manifest_entries (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    manifest_id      INTEGER NOT NULL REFERENCES manifests(id),
    file_id          INTEGER REFERENCES files(id),
    file_version_id  INTEGER REFERENCES file_versions(id),
    original_path    TEXT NOT NULL,       -- 规范化后的完整路径
    hash             TEXT NOT NULL        -- 冗余存储，方便查询
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_files_user ON files(user_name);
CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);
CREATE INDEX IF NOT EXISTS idx_file_versions_file_id ON file_versions(file_id);
CREATE INDEX IF NOT EXISTS idx_file_versions_hash ON file_versions(hash);
-- 复合索引：优化查询最新版本
CREATE INDEX IF NOT EXISTS idx_file_versions_file_version 
    ON file_versions(file_id, version_num DESC);
-- Manifest 相关索引
CREATE INDEX IF NOT EXISTS idx_manifests_user ON manifests(user_name);
CREATE INDEX IF NOT EXISTS idx_manifests_status ON manifests(status);
CREATE INDEX IF NOT EXISTS idx_manifests_job_id ON manifests(job_id);
CREATE INDEX IF NOT EXISTS idx_manifest_entries_manifest ON manifest_entries(manifest_id);
CREATE INDEX IF NOT EXISTS idx_manifest_entries_hash ON manifest_entries(hash);
"""


class Database:
    """
    数据库管理类
    
    支持 SQLite（demo）/ PostgreSQL（生产）
    """
    
    def __init__(self, db_path: str | Path):
        """
        初始化数据库连接
        
        Args:
            db_path: 数据库文件路径（SQLite）或连接字符串（PostgreSQL）
        """
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None
    
    def connect(self) -> sqlite3.Connection:
        """建立数据库连接"""
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False
            )
            # 启用外键约束
            self._conn.execute("PRAGMA foreign_keys = ON")
            # 返回字典形式的结果
            self._conn.row_factory = sqlite3.Row
        return self._conn
    
    def close(self) -> None:
        """关闭数据库连接"""
        if self._conn:
            self._conn.close()
            self._conn = None
    
    def init_schema(self) -> None:
        """初始化数据库 schema"""
        conn = self.connect()
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    
    @contextmanager
    def transaction(self):
        """
        事务上下文管理器
        
        用法：
            with db.transaction():
                # 执行数据库操作
                pass
        """
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    
    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """执行 SQL 语句"""
        conn = self.connect()
        return conn.execute(sql, params)
    
    def fetchone(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        """查询单条记录"""
        cursor = self.execute(sql, params)
        return cursor.fetchone()
    
    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        """查询多条记录"""
        cursor = self.execute(sql, params)
        return cursor.fetchall()
    
    def __enter__(self):
        """支持 with 语句"""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出 with 语句时关闭连接"""
        self.close()
        return False
