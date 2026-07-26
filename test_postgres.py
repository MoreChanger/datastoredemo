"""
PostgreSQL 连接测试脚本

验证：
1. 数据库连接
2. Schema 初始化
3. 基本 CRUD 操作
4. 连接池工作正常
"""

import sys
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent))

from src.storage.db import Database, DatabaseConfig, DatabaseType

def test_postgres_connection():
    """Test PostgreSQL Connection"""
    print("=" * 60)
    print("Test PostgreSQL Connection")
    print("=" * 60)
    
    # 使用 PostgreSQL 配置
    config = DatabaseConfig(
        db_type=DatabaseType.POSTGRESQL,
        postgres_host="localhost",
        postgres_port=5432,
        postgres_user="loongclouds",
        postgres_password="loongclouds_dev_password",
        postgres_database="storage",
        postgres_min_connections=2,
        postgres_max_connections=10,
    )
    
    db = Database(config)
    
    try:
        # 连接数据库
        print("\n1. Connect to database...")
        db.connect()
        print("   [OK] Connected successfully")
        
        # 初始化 schema
        print("\n2. Initialize Schema...")
        db.init_schema()
        print("   [OK] Schema initialized")
        
        # 测试基本操作
        print("\n3. Test basic operations...")
        
        # 插入文件记录（使用 UPSERT）
        file_id = db.upsert(
            "files",
            {
                "user_name": "test_user",
                "path": "D/projects/test.txt",
                "status": "active",
            },
            conflict_columns=["user_name", "path"]
        )
        print(f"   [OK] Insert file record: id={file_id}")
        
        # 查询文件
        row = db.fetchone(
            "SELECT * FROM files WHERE user_name = %s AND path = %s",
            ("test_user", "D/projects/test.txt")
        )
        print(f"   [OK] Query file: path={row['path']}, status={row['status']}")
        
        # 插入版本记录
        version_id = db.upsert(
            "file_versions",
            {
                "file_id": file_id,
                "version_num": 1,
                "hash": "a" * 64,  # 模拟 SHA-256
                "size": 1024,
                "uploaded_by": "test_user",
            },
            conflict_columns=["file_id", "version_num"]
        )
        print(f"   [OK] Insert version record: id={version_id}")
        
        # 测试事务
        print("\n4. Test transaction...")
        with db.transaction():
            db.execute(
                "UPDATE files SET project = %s WHERE id = %s",
                ("test_project", file_id)
            )
        print("   [OK] Transaction executed successfully")
        
        # 测试行锁
        print("\n5. Test row lock (SELECT FOR UPDATE)...")
        with db.lock_row("files", "id = %s", (file_id,)):
            row = db.fetchone("SELECT * FROM files WHERE id = %s", (file_id,))
            print(f"   [OK] Row lock acquired: path={row['path']}")
        
        # 测试引用计数视图
        print("\n6. Test reference count view...")
        ref_counts = db.fetchall("SELECT * FROM blob_ref_counts LIMIT 5")
        print(f"   [OK] Reference count view: {len(ref_counts)} records")
        
        # 清理测试数据
        print("\n7. Cleanup test data...")
        db.execute("DELETE FROM file_versions WHERE file_id = %s", (file_id,))
        db.execute("DELETE FROM files WHERE id = %s", (file_id,))
        print("   [OK] Test data cleaned up")
        
        print("\n" + "=" * 60)
        print("ALL TESTS PASSED!")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n[FAIL] Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        db.close()
    
    return True


if __name__ == "__main__":
    success = test_postgres_connection()
    sys.exit(0 if success else 1)
