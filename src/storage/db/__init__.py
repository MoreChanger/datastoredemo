"""
数据库抽象层 - 支持 SQLite 和 PostgreSQL

特性：
- 连接池管理（PostgreSQL 使用 psycopg_pool）
- WAL 支持（PostgreSQL 原生支持）
- 事务管理
- 并发竞态防护
"""

from __future__ import annotations

import os
import sqlite3
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Union

# PostgreSQL support (optional)
try:
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    HAS_POSTGRES = True
except ImportError:
    psycopg = None
    sql = None
    dict_row = None
    ConnectionPool = None
    HAS_POSTGRES = False


class DatabaseType(Enum):
    """数据库类型"""
    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"


@dataclass
class DatabaseConfig:
    """数据库配置"""
    db_type: DatabaseType = DatabaseType.SQLITE
    
    # SQLite 配置
    sqlite_path: str = "data/storage.db"
    
    # PostgreSQL 配置
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "loongclouds"
    postgres_password: str = "loongclouds_dev_password"
    postgres_database: str = "storage"
    postgres_min_connections: int = 2
    postgres_max_connections: int = 10
    
    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        """从环境变量加载配置"""
        db_type_str = os.getenv("DB_TYPE", "sqlite").lower()
        db_type = DatabaseType.POSTGRESQL if db_type_str == "postgresql" else DatabaseType.SQLITE
        
        return cls(
            db_type=db_type,
            sqlite_path=os.getenv("SQLITE_PATH", "data/storage.db"),
            postgres_host=os.getenv("POSTGRES_HOST", "localhost"),
            postgres_port=int(os.getenv("POSTGRES_PORT", "5432")),
            postgres_user=os.getenv("POSTGRES_USER", "loongclouds"),
            postgres_password=os.getenv("POSTGRES_PASSWORD", "loongclouds_dev_password"),
            postgres_database=os.getenv("POSTGRES_DATABASE", "storage"),
            postgres_min_connections=int(os.getenv("POSTGRES_MIN_CONNECTIONS", "2")),
            postgres_max_connections=int(os.getenv("POSTGRES_MAX_CONNECTIONS", "10")),
        )
    
    def get_postgres_dsn(self) -> str:
        """获取 PostgreSQL 连接字符串"""
        return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_database}"


class DatabaseBackend(ABC):
    """数据库后端抽象基类"""
    
    @abstractmethod
    def connect(self) -> None:
        """建立连接"""
        pass
    
    @abstractmethod
    def close(self) -> None:
        """关闭连接"""
        pass
    
    @abstractmethod
    def execute(self, query: str, params: tuple = ()) -> Any:
        """执行 SQL"""
        pass
    
    @abstractmethod
    def fetchone(self, query: str, params: tuple = ()) -> Optional[dict]:
        """查询单条记录"""
        pass
    
    @abstractmethod
    def fetchall(self, query: str, params: tuple = ()) -> list[dict]:
        """查询多条记录"""
        pass
    
    @abstractmethod
    def executemany(self, query: str, params_list: list[tuple]) -> None:
        """批量执行"""
        pass
    
    @abstractmethod
    @contextmanager
    def transaction(self):
        """事务上下文"""
        pass
    
    @abstractmethod
    @contextmanager
    def lock_row(self, table: str, condition: str, params: tuple = ()):
        """行锁（SELECT ... FOR UPDATE）"""
        pass
    
    @abstractmethod
    def upsert(self, table: str, data: dict, conflict_columns: list[str], update_columns: Optional[list[str]] = None) -> int:
        """UPSERT 操作"""
        pass


class SQLiteBackend(DatabaseBackend):
    """SQLite 后端"""
    
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None
    
    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False
            )
            # 启用外键约束
            self._conn.execute("PRAGMA foreign_keys = ON")
            # 启用 WAL 模式（SQLite 的 WAL）
            self._conn.execute("PRAGMA journal_mode = WAL")
            # 返回字典形式的结果
            self._conn.row_factory = sqlite3.Row
        return self._conn
    
    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
    
    def execute(self, query: str, params: tuple = ()) -> sqlite3.Cursor:
        conn = self.connect()
        return conn.execute(query, params)
    
    def fetchone(self, query: str, params: tuple = ()) -> Optional[dict]:
        cursor = self.execute(query, params)
        row = cursor.fetchone()
        return dict(row) if row else None
    
    def fetchall(self, query: str, params: tuple = ()) -> list[dict]:
        cursor = self.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]
    
    def executemany(self, query: str, params_list: list[tuple]) -> None:
        conn = self.connect()
        conn.executemany(query, params_list)
    
    @contextmanager
    def transaction(self):
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    
    @contextmanager
    def lock_row(self, table: str, condition: str, params: tuple = ()):
        """
        SQLite 没有真正的行锁，使用 BEGIN IMMEDIATE 模拟
        注意：SQLite 的锁是数据库级别的，不是行级别
        """
        conn = self.connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            # 查询行以确保存在
            self.fetchone(f"SELECT * FROM {table} WHERE {condition} LIMIT 1", params)
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    
    def upsert(self, table: str, data: dict, conflict_columns: list[str], update_columns: Optional[list[str]] = None) -> int:
        """
        SQLite UPSERT (INSERT ... ON CONFLICT DO UPDATE)
        """
        columns = list(data.keys())
        placeholders = ", ".join(["?"] * len(columns))
        column_names = ", ".join(columns)
        
        if update_columns is None:
            update_columns = [c for c in columns if c not in conflict_columns]
        
        if update_columns:
            update_clause = ", ".join([f"{c} = excluded.{c}" for c in update_columns])
            query = f"""
                INSERT INTO {table} ({column_names})
                VALUES ({placeholders})
                ON CONFLICT ({', '.join(conflict_columns)})
                DO UPDATE SET {update_clause}
                RETURNING id
            """
        else:
            query = f"""
                INSERT INTO {table} ({column_names})
                VALUES ({placeholders})
                ON CONFLICT ({', '.join(conflict_columns)})
                DO NOTHING
                RETURNING id
            """
        
        result = self.fetchone(query, tuple(data.values()))
        return result["id"] if result else self.fetchone(
            f"SELECT id FROM {table} WHERE {' AND '.join([f'{c} = ?' for c in conflict_columns])}",
            tuple(data[c] for c in conflict_columns)
        )["id"]


class PostgreSQLBackend(DatabaseBackend):
    """PostgreSQL 后端（连接池 + WAL）"""
    
    def __init__(self, config: DatabaseConfig):
        if not HAS_POSTGRES:
            raise RuntimeError("psycopg not installed. Run: pip install psycopg[binary] psycopg-pool")
        
        self.config = config
        self._pool: Optional[ConnectionPool] = None
    
    def _translate_placeholders(self, query: str) -> str:
        """
        将 SQLite 风格的 ? 占位符转换为 PostgreSQL 的 %s
        
        PostgreSQL 使用 %s 作为占位符，而 SQLite 使用 ?
        此方法自动转换，保持代码向后兼容
        """
        return query.replace("?", "%s")
    
    def connect(self) -> ConnectionPool:
        if self._pool is None:
            self._pool = ConnectionPool(
                conninfo=self.config.get_postgres_dsn(),
                min_size=self.config.postgres_min_connections,
                max_size=self.config.postgres_max_connections,
                kwargs={"row_factory": dict_row},
                open=True  # 显式打开连接池
            )
        return self._pool
    
    def close(self) -> None:
        if self._pool:
            self._pool.close()
            self._pool = None
    
    def execute(self, query: str, params: tuple = ()) -> Any:
        pool = self.connect()
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(self._translate_placeholders(query), params)
                return cur
    
    def fetchone(self, query: str, params: tuple = ()) -> Optional[dict]:
        pool = self.connect()
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(self._translate_placeholders(query), params)
                return cur.fetchone()
    
    def fetchall(self, query: str, params: tuple = ()) -> list[dict]:
        pool = self.connect()
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(self._translate_placeholders(query), params)
                return cur.fetchall()
    
    def executemany(self, query: str, params_list: list[tuple]) -> None:
        pool = self.connect()
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(self._translate_placeholders(query), params_list)
    
    @contextmanager
    def transaction(self):
        pool = self.connect()
        with pool.connection() as conn:
            with conn.transaction():
                yield conn
    
    @contextmanager
    def lock_row(self, table: str, condition: str, params: tuple = ()):
        """
        PostgreSQL 行锁（SELECT ... FOR UPDATE）
        这是真正的行级锁，可以防止并发竞态
        """
        pool = self.connect()
        with pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    # 加行锁（转换占位符）
                    translated_condition = self._translate_placeholders(condition)
                    cur.execute(f"SELECT * FROM {table} WHERE {translated_condition} FOR UPDATE", params)
                    row = cur.fetchone()
                    yield conn
    
    def upsert(self, table: str, data: dict, conflict_columns: list[str], update_columns: Optional[list[str]] = None) -> int:
        """
        PostgreSQL UPSERT (INSERT ... ON CONFLICT DO UPDATE)
        使用 psycopg.sql 安全构建 SQL
        """
        if update_columns is None:
            update_columns = [c for c in data.keys() if c not in conflict_columns]
        
        pool = self.connect()
        with pool.connection() as conn:
            with conn.cursor() as cur:
                # 构建安全 SQL
                columns = [sql.Identifier(c) for c in data.keys()]
                placeholders = [sql.Placeholder(c) for c in data.keys()]
                conflict_targets = [sql.Identifier(c) for c in conflict_columns]
                
                if update_columns:
                    update_excluded = [
                        sql.SQL("{} = {}").format(sql.Identifier(c), sql.SQL(f"EXCLUDED.{c}"))
                        for c in update_columns
                    ]
                    query = sql.SQL("""
                        INSERT INTO {} ({})
                        VALUES ({})
                        ON CONFLICT ({})
                        DO UPDATE SET {}
                        RETURNING id
                    """).format(
                        sql.Identifier(table),
                        sql.SQL(", ").join(columns),
                        sql.SQL(", ").join(placeholders),
                        sql.SQL(", ").join(conflict_targets),
                        sql.SQL(", ").join(update_excluded)
                    )
                else:
                    query = sql.SQL("""
                        INSERT INTO {} ({})
                        VALUES ({})
                        ON CONFLICT ({})
                        DO NOTHING
                        RETURNING id
                    """).format(
                        sql.Identifier(table),
                        sql.SQL(", ").join(columns),
                        sql.SQL(", ").join(placeholders),
                        sql.SQL(", ").join(conflict_targets)
                    )
                
                cur.execute(query, data)
                result = cur.fetchone()
                
                if result:
                    return result["id"]
                else:
                    # 冲突时查询现有 ID
                    where_clause = sql.SQL(" AND ").join([
                        sql.SQL("{} = {}").format(sql.Identifier(c), sql.Placeholder(c))
                        for c in conflict_columns
                    ])
                    query = sql.SQL("SELECT id FROM {} WHERE {}").format(
                        sql.Identifier(table),
                        where_clause
                    )
                    cur.execute(query, {c: data[c] for c in conflict_columns})
                    return cur.fetchone()["id"]


class Database:
    """
    统一数据库接口
    
    自动根据配置选择后端：
    - SQLite: 适用于开发测试
    - PostgreSQL: 适用于生产环境（连接池 + WAL）
    
    向后兼容：
    - Database(path) → SQLite 模式（path 为数据库文件路径）
    - Database(config=DatabaseConfig(...)) → 使用完整配置
    """
    
    # 使用 context variables 跟踪当前事务连接
    _current_conn = None
    
    def __init__(self, db_path: Optional[Union[str, Path]] = None, config: Optional[DatabaseConfig] = None):
        """
        初始化数据库
        
        Args:
            db_path: SQLite 数据库文件路径（向后兼容）
            config: 完整数据库配置（新 API）
        """
        if db_path is not None:
            # 向后兼容模式：使用 SQLite
            self.config = DatabaseConfig(
                db_type=DatabaseType.SQLITE,
                sqlite_path=str(db_path)
            )
        else:
            # 新 API：使用配置对象或从环境变量加载
            self.config = config or DatabaseConfig.from_env()
        self._backend: Optional[DatabaseBackend] = None
        self._tx_conn = None  # 当前事务连接
    
    @property
    def backend(self) -> DatabaseBackend:
        if self._backend is None:
            if self.config.db_type == DatabaseType.SQLITE:
                self._backend = SQLiteBackend(self.config.sqlite_path)
            else:
                self._backend = PostgreSQLBackend(self.config)
        return self._backend
    
    def connect(self) -> None:
        self.backend.connect()
    
    def close(self) -> None:
        if self._backend:
            self._backend.close()
    
    def init_schema(self) -> None:
        """初始化数据库 schema"""
        schema_path = Path(__file__).parent.parent.parent / "scripts" / "init.sql"
        
        if self.config.db_type == DatabaseType.POSTGRESQL:
            # PostgreSQL: 执行 init.sql
            if schema_path.exists():
                schema_sql = schema_path.read_text(encoding="utf-8")
                pool = self.backend.connect()
                with pool.connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(schema_sql)
        else:
            # SQLite: 使用内嵌 schema（从旧的 database.py 导入）
            from ..database import SCHEMA_SQL
            conn = self.backend.connect()
            conn.executescript(SCHEMA_SQL)
    
    def _get_execute_conn(self):
        """获取执行查询的连接（优先使用当前事务连接）"""
        if self._tx_conn is not None:
            return self._tx_conn
        return None  # 后端会自动获取连接
    
    def execute(self, query: str, params: tuple = ()) -> Any:
        # 如果在事务中，使用事务连接
        if self._tx_conn is not None:
            if self.config.db_type == DatabaseType.POSTGRESQL:
                query = query.replace("?", "%s")
            cursor = self._tx_conn.cursor()
            cursor.execute(query, params)
            return cursor
        return self.backend.execute(query, params)
    
    def fetchone(self, query: str, params: tuple = ()) -> Optional[dict]:
        # 如果在事务中，使用事务连接
        if self._tx_conn is not None:
            if self.config.db_type == DatabaseType.POSTGRESQL:
                query = query.replace("?", "%s")
            cursor = self._tx_conn.cursor()
            cursor.execute(query, params)
            row = cursor.fetchone()
            return dict(row) if row else None
        return self.backend.fetchone(query, params)
    
    def fetchall(self, query: str, params: tuple = ()) -> list[dict]:
        # 如果在事务中，使用事务连接
        if self._tx_conn is not None:
            if self.config.db_type == DatabaseType.POSTGRESQL:
                query = query.replace("?", "%s")
            cursor = self._tx_conn.cursor()
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
        return self.backend.fetchall(query, params)
    
    def executemany(self, query: str, params_list: list[tuple]) -> None:
        if self._tx_conn is not None:
            if self.config.db_type == DatabaseType.POSTGRESQL:
                query = query.replace("?", "%s")
            cursor = self._tx_conn.cursor()
            cursor.executemany(query, params_list)
        else:
            self.backend.executemany(query, params_list)
    
    @contextmanager
    def transaction(self):
        """事务上下文"""
        if self.config.db_type == DatabaseType.SQLITE:
            with self.backend.transaction() as conn:
                self._tx_conn = conn
                try:
                    yield conn
                finally:
                    self._tx_conn = None
        else:
            # PostgreSQL: 使用连接池
            pool = self.backend.connect()
            with pool.connection() as conn:
                with conn.transaction():
                    self._tx_conn = conn
                    try:
                        yield conn
                    finally:
                        self._tx_conn = None
    
    @contextmanager
    def lock_row(self, table: str, condition: str, params: tuple = ()):
        """
        行锁上下文（SELECT ... FOR UPDATE）
        
        SQLite: 使用 BEGIN IMMEDIATE（数据库级锁）
        PostgreSQL: 使用 SELECT FOR UPDATE（行级锁）
        """
        if self.config.db_type == DatabaseType.SQLITE:
            # SQLite: BEGIN IMMEDIATE 提供数据库级锁
            conn = self.backend.connect()
            conn.execute("BEGIN IMMEDIATE")
            self._tx_conn = conn
            try:
                # 查询行
                cursor = conn.cursor()
                cursor.execute(f"SELECT * FROM {table} WHERE {condition} LIMIT 1", params)
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                self._tx_conn = None
        else:
            # PostgreSQL: SELECT FOR UPDATE
            pool = self.backend.connect()
            with pool.connection() as conn:
                with conn.transaction():
                    self._tx_conn = conn
                    try:
                        # 转换占位符
                        translated_condition = condition.replace("?", "%s")
                        cursor = conn.cursor()
                        cursor.execute(f"SELECT * FROM {table} WHERE {translated_condition} FOR UPDATE", params)
                        yield conn
                    finally:
                        self._tx_conn = None
    
    def upsert(self, table: str, data: dict, conflict_columns: list[str], update_columns: Optional[list[str]] = None) -> int:
        return self.backend.upsert(table, data, conflict_columns, update_columns)
    
    def __enter__(self):
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


# 向后兼容：保留旧的 Database 类接口
# 新代码应使用 DatabaseBackend 或 Database 抽象层
__all__ = [
    "DatabaseType",
    "DatabaseConfig", 
    "DatabaseBackend",
    "SQLiteBackend",
    "PostgreSQLBackend",
    "Database",
    "HAS_POSTGRES",
]
