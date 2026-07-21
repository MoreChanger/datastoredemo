#!/usr/bin/env python
"""
存储系统 Demo CLI

验证功能：
- P0: Blob 存储（SHA-256 去重）、文件上传与版本管理
- P1: 哈希缓存优化、用户目录链接
- P2: Manifest 快照、引用计数清理

用法：
    python demo.py init                    # 初始化存储
    python demo.py upload <file> [path]    # 上传文件
    python demo.py info <path>             # 查看文件信息
    python demo.py history <path>          # 查看版本历史
    python demo.py list                    # 列出所有文件
    python demo.py stats                   # 查看存储统计
    python demo.py cache-stats             # 查看缓存统计
    python demo.py cache-clear             # 清空缓存
    python demo.py job create <file>...    # 创建快照
    python demo.py job info <job_id>       # 查看快照信息
    python demo.py job list                # 列出快照
    python demo.py cleanup-report          # 查看清理报告
    python demo.py cleanup [--dry-run]     # 清理零引用 blob
"""

import sys
import os
import argparse
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent))

from src.storage import (
    BlobStorage, Database, FileManager, HashCache, compute_sha256,
    ManifestManager, StorageCleaner
)


# 默认配置
DEFAULT_STORAGE_PATH = Path(__file__).parent / "data" / "storage"
DEFAULT_USERS_PATH = Path(__file__).parent / "data" / "users"
DEFAULT_JOBS_PATH = Path(__file__).parent / "data" / "jobs"
DEFAULT_OUTPUTS_PATH = Path(__file__).parent / "data" / "outputs"
DEFAULT_TRASH_PATH = Path(__file__).parent / "data" / "trash"
DEFAULT_DB_PATH = Path(__file__).parent / "data" / "storage.db"
DEFAULT_CACHE_PATH = Path(__file__).parent / "data" / "hash_cache.json"
DEFAULT_USER = "demo_user"


def get_file_manager() -> FileManager:
    """获取文件管理器实例（带缓存）"""
    blob_storage = BlobStorage(DEFAULT_STORAGE_PATH)
    db = Database(DEFAULT_DB_PATH)
    hash_cache = HashCache(DEFAULT_CACHE_PATH)
    fm = FileManager(db, blob_storage, DEFAULT_USERS_PATH, hash_cache)
    return fm


def get_manifest_manager() -> ManifestManager:
    """获取快照管理器实例"""
    blob_storage = BlobStorage(DEFAULT_STORAGE_PATH)
    db = Database(DEFAULT_DB_PATH)
    hash_cache = HashCache(DEFAULT_CACHE_PATH)
    fm = FileManager(db, blob_storage, DEFAULT_USERS_PATH, hash_cache)
    mm = ManifestManager(db, fm, DEFAULT_JOBS_PATH, DEFAULT_OUTPUTS_PATH)
    return mm


def get_cleaner() -> StorageCleaner:
    """获取清理器实例"""
    blob_storage = BlobStorage(DEFAULT_STORAGE_PATH)
    db = Database(DEFAULT_DB_PATH)
    cleaner = StorageCleaner(db, blob_storage, DEFAULT_TRASH_PATH)
    return cleaner


def cmd_init(args):
    """初始化存储"""
    print("=" * 60)
    print("初始化存储系统")
    print("=" * 60)
    
    # 创建目录
    DEFAULT_STORAGE_PATH.mkdir(parents=True, exist_ok=True)
    DEFAULT_USERS_PATH.mkdir(parents=True, exist_ok=True)
    DEFAULT_JOBS_PATH.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUTPUTS_PATH.mkdir(parents=True, exist_ok=True)
    DEFAULT_TRASH_PATH.mkdir(parents=True, exist_ok=True)
    
    # 初始化
    blob_storage = BlobStorage(DEFAULT_STORAGE_PATH)
    blob_storage.init_storage()
    
    db = Database(DEFAULT_DB_PATH)
    db.init_schema()
    
    # 初始化缓存
    hash_cache = HashCache(DEFAULT_CACHE_PATH)
    
    print(f"[OK] 存储目录: {DEFAULT_STORAGE_PATH}")
    print(f"[OK] 用户目录: {DEFAULT_USERS_PATH}")
    print(f"[OK] Job 目录: {DEFAULT_JOBS_PATH}")
    print(f"[OK] 输出目录: {DEFAULT_OUTPUTS_PATH}")
    print(f"[OK] 回收站: {DEFAULT_TRASH_PATH}")
    print(f"[OK] 数据库: {DEFAULT_DB_PATH}")
    print(f"[OK] 缓存文件: {DEFAULT_CACHE_PATH}")
    print("\n初始化完成！")


def cmd_upload(args):
    """上传文件"""
    local_path = Path(args.file)
    
    if not local_path.exists():
        print(f"错误: 文件不存在 - {local_path}")
        return 1
    
    fm = get_file_manager()
    
    server_path = args.path if args.path else None
    
    print(f"\n上传文件: {local_path}")
    if server_path:
        print(f"目标路径: {server_path}")
    
    result = fm.upload_file(
        local_path=local_path,
        user_name=DEFAULT_USER,
        server_path=server_path
    )
    
    print("\n" + "=" * 60)
    print("上传结果")
    print("=" * 60)
    print(f"路径:   {result.path}")
    print(f"哈希:   {result.hash[:32]}...")
    print(f"版本:   v{result.version}")
    print(f"大小:   {result.size} bytes ({result.size / 1024:.2f} KB)")
    print(f"状态:   {'新版本' if result.created else '内容未变'}")
    print(f"去重:   {'是 (复用已有blob)' if result.deduplicated else '否 (新blob)'}")
    print(f"缓存:   {'命中 (跳过哈希计算)' if result.cache_hit else '未命中 (已计算哈希)'}")
    
    return 0


def cmd_info(args):
    """查看文件信息"""
    fm = get_file_manager()
    
    info = fm.get_file_info(DEFAULT_USER, args.path)
    
    if info is None:
        print(f"文件不存在: {args.path}")
        return 1
    
    print("\n" + "=" * 60)
    print("文件信息")
    print("=" * 60)
    print(f"ID:           {info.file_id}")
    print(f"路径:         {info.path}")
    print(f"状态:         {info.status}")
    print(f"当前版本:     v{info.current_version}")
    print(f"当前哈希:     {info.current_hash[:32]}...")
    print(f"当前大小:     {info.current_size} bytes")
    print(f"创建时间:     {info.created_at}")
    if info.updated_at:
        print(f"更新时间:     {info.updated_at}")
    
    return 0


def cmd_history(args):
    """查看版本历史"""
    fm = get_file_manager()
    
    history = fm.get_version_history(DEFAULT_USER, args.path, limit=args.limit)
    
    if not history:
        print(f"文件不存在或无版本记录: {args.path}")
        return 1
    
    print("\n" + "=" * 60)
    print(f"版本历史: {args.path}")
    print("=" * 60)
    
    for v in history:
        print(f"\n版本 {v['version_num']}:")
        print(f"  哈希:     {v['hash'][:32]}...")
        print(f"  大小:     {v['size']} bytes")
        print(f"  上传者:   {v['uploaded_by']}")
        print(f"  时间:     {v['uploaded_at']}")
    
    return 0


def cmd_list(args):
    """列出所有文件"""
    fm = get_file_manager()
    
    files = fm.list_user_files(DEFAULT_USER)
    
    if not files:
        print("无文件记录")
        return 0
    
    print("\n" + "=" * 60)
    print(f"文件列表 (用户: {DEFAULT_USER})")
    print("=" * 60)
    
    for f in files:
        print(f"\n{f['path']}")
        print(f"  版本: v{f['version_num']}  大小: {f['size']} bytes  哈希: {f['hash'][:16]}...")
    
    print(f"\n共 {len(files)} 个文件")
    
    return 0


def cmd_stats(args):
    """查看存储统计"""
    fm = get_file_manager()
    
    stats = fm.get_storage_stats()
    
    print("\n" + "=" * 60)
    print("存储统计")
    print("=" * 60)
    print(f"文件数量:     {stats['file_count']}")
    print(f"版本数量:     {stats['version_count']}")
    print(f"Blob 数量:    {stats['blob_count']}")
    print(f"总大小:       {stats['total_size_bytes']} bytes ({stats['total_size_mb']:.2f} MB)")
    print(f"平均去重率:   {(1 - stats['blob_count'] / max(stats['version_count'], 1)) * 100:.1f}%")
    
    return 0


def cmd_hash(args):
    """计算文件哈希"""
    file_path = Path(args.file)
    
    if not file_path.exists():
        print(f"错误: 文件不存在 - {file_path}")
        return 1
    
    print(f"\n计算文件哈希: {file_path}")
    file_hash = compute_sha256(file_path)
    file_size = file_path.stat().st_size
    
    print(f"SHA-256: {file_hash}")
    print(f"大小:    {file_size} bytes ({file_size / 1024:.2f} KB)")
    
    return 0


def cmd_cache_stats(args):
    """查看缓存统计"""
    hash_cache = HashCache(DEFAULT_CACHE_PATH)
    stats = hash_cache.get_stats()
    
    print("\n" + "=" * 60)
    print("缓存统计")
    print("=" * 60)
    print(f"缓存条目:     {stats['entry_count']}")
    print(f"缓存文件:     {stats['cache_file']}")
    if stats['entry_count'] > 0:
        print(f"最旧条目:     {stats['oldest_entry_age']:.1f} 秒前")
        print(f"最新条目:     {stats['newest_entry_age']:.1f} 秒前")
    
    return 0


def cmd_cache_clear(args):
    """清空缓存"""
    hash_cache = HashCache(DEFAULT_CACHE_PATH)
    hash_cache.clear()
    
    print("\n" + "=" * 60)
    print("缓存已清空")
    print("=" * 60)
    
    return 0


# ========== P2 命令 ==========

def cmd_job_create(args):
    """创建快照"""
    mm = get_manifest_manager()
    mm.init_directories()
    
    # 解析文件路径
    file_paths = args.files
    
    print(f"\n创建快照，包含 {len(file_paths)} 个文件")
    
    manifest = mm.create_manifest(
        user_name=DEFAULT_USER,
        file_paths=file_paths,
        scene_path=args.scene,
        job_id=args.job_id
    )
    
    print("\n" + "=" * 60)
    print("快照创建成功")
    print("=" * 60)
    print(f"Job ID:     {manifest.job_id}")
    print(f"用户:       {manifest.user_name}")
    print(f"文件数量:   {manifest.file_count}")
    print(f"总大小:     {manifest.total_size} bytes ({manifest.total_size / 1024:.2f} KB)")
    print(f"状态:       {manifest.status}")
    print(f"创建时间:   {manifest.created_at}")
    
    return 0


def cmd_job_info(args):
    """查看快照信息"""
    mm = get_manifest_manager()
    
    info = mm.get_manifest(args.job_id)
    
    if info is None:
        print(f"快照不存在: {args.job_id}")
        return 1
    
    entries = mm.get_manifest_entries(args.job_id)
    
    print("\n" + "=" * 60)
    print(f"快照信息: {args.job_id}")
    print("=" * 60)
    print(f"用户:       {info.user_name}")
    print(f"场景:       {info.scene_path or '(无)'}")
    print(f"状态:       {info.status}")
    print(f"文件数量:   {info.file_count}")
    print(f"总大小:     {info.total_size} bytes")
    print(f"创建时间:   {info.created_at}")
    
    if entries:
        print(f"\n文件列表 ({len(entries)} 个):")
        for e in entries[:10]:  # 只显示前 10 个
            print(f"  {e.original_path}")
            print(f"    版本: v{e.version_num}  哈希: {e.hash[:16]}...")
        if len(entries) > 10:
            print(f"  ... 还有 {len(entries) - 10} 个文件")
    
    return 0


def cmd_job_list(args):
    """列出快照"""
    mm = get_manifest_manager()
    
    manifests = mm.list_manifests(
        user_name=args.user if args.user != "all" else None,
        status=args.status,
        limit=args.limit
    )
    
    if not manifests:
        print("无快照记录")
        return 0
    
    print("\n" + "=" * 60)
    print("快照列表")
    print("=" * 60)
    
    for m in manifests:
        print(f"\n{m.job_id}")
        print(f"  用户: {m.user_name}  文件: {m.file_count}  状态: {m.status}")
        print(f"  大小: {m.total_size} bytes  时间: {m.created_at}")
    
    print(f"\n共 {len(manifests)} 个快照")
    
    return 0


def cmd_cleanup_report(args):
    """查看清理报告"""
    cleaner = get_cleaner()
    cleaner.init_trash()
    
    report = cleaner.get_cleanup_report()
    
    print("\n" + "=" * 60)
    print("清理报告")
    print("=" * 60)
    
    print("\n[Blob 存储]")
    print(f"  总数量:       {report['blobs']['total_count']}")
    print(f"  活跃引用:     {report['blobs']['active_count']}")
    print(f"  零引用:       {report['blobs']['zero_ref_count']}")
    print(f"  总大小:       {report['blobs']['total_size_mb']:.2f} MB")
    
    print("\n[回收站]")
    print(f"  文件数量:     {report['trash']['file_count']}")
    print(f"  总大小:       {report['trash']['total_size_mb']:.2f} MB")
    print(f"  最旧文件:     {report['trash']['oldest_age_hours']:.1f} 小时前")
    print(f"  保留时间:     {report['trash']['grace_hours']} 小时")
    
    print("\n[建议]")
    if report['recommendations']['can_cleanup']:
        print(f"  - 可清理 {report['blobs']['zero_ref_count']} 个零引用 blob")
    if report['recommendations']['can_empty_trash']:
        print(f"  - 回收站有 {report['trash']['file_count']} 个文件可清空")
    
    return 0


def cmd_cleanup(args):
    """清理零引用 blob"""
    cleaner = get_cleaner()
    cleaner.init_trash()
    
    if args.dry_run:
        print("\n[演练模式] 不实际移动文件")
    
    stats = cleaner.cleanup_zero_refs(dry_run=args.dry_run)
    
    print("\n" + "=" * 60)
    print("清理结果")
    print("=" * 60)
    
    if args.dry_run:
        print(f"将移动:   {stats.get('would_move', 0)} 个文件")
    else:
        print(f"已移动:   {stats.get('moved', 0)} 个文件到回收站")
        print(f"失败:     {stats.get('failed', 0)} 个文件")
    
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="存储系统 Demo CLI - 验证 P0/P1/P2 功能",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python demo.py init                         # 初始化存储
  python demo.py upload test.txt              # 上传文件
  python demo.py upload test.txt D/data.txt   # 指定目标路径
  python demo.py info D/data.txt              # 查看文件信息
  python demo.py history D/data.txt           # 查看版本历史
  python demo.py list                         # 列出所有文件
  python demo.py stats                        # 查看存储统计
  python demo.py hash test.txt                # 计算文件哈希
  python demo.py cache-stats                  # 查看缓存统计
  python demo.py cache-clear                  # 清空缓存
  python demo.py job create D/file1.txt D/file2.txt  # 创建快照
  python demo.py job info job_xxx             # 查看快照信息
  python demo.py job list                     # 列出快照
  python demo.py cleanup-report               # 查看清理报告
  python demo.py cleanup --dry-run            # 演练清理
  python demo.py cleanup                      # 执行清理
        """
    )
    
    subparsers = parser.add_subparsers(dest="command", help="可用命令")
    
    # init
    subparsers.add_parser("init", help="初始化存储系统")
    
    # upload
    upload_parser = subparsers.add_parser("upload", help="上传文件")
    upload_parser.add_argument("file", help="本地文件路径")
    upload_parser.add_argument("path", nargs="?", help="服务器存储路径（可选）")
    
    # info
    info_parser = subparsers.add_parser("info", help="查看文件信息")
    info_parser.add_argument("path", help="文件路径")
    
    # history
    history_parser = subparsers.add_parser("history", help="查看版本历史")
    history_parser.add_argument("path", help="文件路径")
    history_parser.add_argument("-l", "--limit", type=int, default=10, help="显示数量")
    
    # list
    subparsers.add_parser("list", help="列出所有文件")
    
    # stats
    subparsers.add_parser("stats", help="查看存储统计")
    
    # hash
    hash_parser = subparsers.add_parser("hash", help="计算文件哈希")
    hash_parser.add_argument("file", help="文件路径")
    
    # cache-stats
    subparsers.add_parser("cache-stats", help="查看缓存统计")
    
    # cache-clear
    subparsers.add_parser("cache-clear", help="清空缓存")
    
    # job create
    job_create_parser = subparsers.add_parser("job-create", help="创建快照")
    job_create_parser.add_argument("files", nargs="+", help="文件路径列表")
    job_create_parser.add_argument("--scene", help="主场景文件路径")
    job_create_parser.add_argument("--job-id", help="自定义 Job ID")
    
    # job info
    job_info_parser = subparsers.add_parser("job-info", help="查看快照信息")
    job_info_parser.add_argument("job_id", help="Job ID")
    
    # job list
    job_list_parser = subparsers.add_parser("job-list", help="列出快照")
    job_list_parser.add_argument("--user", default=DEFAULT_USER, help="用户名筛选")
    job_list_parser.add_argument("--status", help="状态筛选")
    job_list_parser.add_argument("--limit", type=int, default=20, help="显示数量")
    
    # cleanup-report
    subparsers.add_parser("cleanup-report", help="查看清理报告")
    
    # cleanup
    cleanup_parser = subparsers.add_parser("cleanup", help="清理零引用 blob")
    cleanup_parser.add_argument("--dry-run", action="store_true", help="演练模式")
    
    args = parser.parse_args()
    
    if args.command is None:
        parser.print_help()
        return 0
    
    # 执行命令
    commands = {
        "init": cmd_init,
        "upload": cmd_upload,
        "info": cmd_info,
        "history": cmd_history,
        "list": cmd_list,
        "stats": cmd_stats,
        "hash": cmd_hash,
        "cache-stats": cmd_cache_stats,
        "cache-clear": cmd_cache_clear,
        "job-create": cmd_job_create,
        "job-info": cmd_job_info,
        "job-list": cmd_job_list,
        "cleanup-report": cmd_cleanup_report,
        "cleanup": cmd_cleanup,
    }
    
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main() or 0)
