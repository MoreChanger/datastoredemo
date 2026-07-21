# PROJECT KNOWLEDGE BASE

**Generated:** 2026-07-22
**Branch:** (not a git repo)

## OVERVIEW

LoongClouds render farm storage system. Content-addressable storage (CAS) for VFX/animation production with SHA-256 blob storage, version management, manifest snapshots, and reference counting cleanup. Python implementation with SQLite metadata store.

## STRUCTURE

```
datastoredemo/
├── src/storage/              # Core storage modules
│   ├── blob_storage.py       # SHA-256 blob storage with dedup
│   ├── file_manager.py       # File upload & version management
│   ├── database.py           # SQLite metadata store
│   ├── hash_cache.py         # Local hash cache optimization
│   ├── manifest_manager.py   # Job snapshot management
│   └── cleaner.py            # Reference counting & cleanup
├── tests/                    # Unit tests (64 tests)
│   ├── test_blob_storage.py
│   ├── test_file_manager.py
│   ├── test_hash_cache.py
│   ├── test_p1_features.py
│   └── test_p2_features.py
├── docs/                     # Design documentation (Chinese)
│   ├── 04-storage-system-design.md
│   └── storage-module-design.md
├── data/                     # Runtime data (created by demo)
│   ├── storage/              # Blob storage directory
│   ├── users/                # User directory
│   ├── jobs/                 # Job snapshots
│   ├── outputs/              # Render outputs
│   ├── trash/                # Recycle bin
│   ├── storage.db            # SQLite database
│   └── hash_cache.json       # Hash cache file
├── demo.py                   # Demo CLI for validation
└── requirements.txt
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Blob storage implementation | `src/storage/blob_storage.py` | SHA-256, dedup, atomic write |
| File upload flow | `src/storage/file_manager.py` | Version tracking, path normalization |
| Database schema | `src/storage/database.py` | files, file_versions, manifests tables |
| Hash cache | `src/storage/hash_cache.py` | mtime+size optimization |
| Manifest management | `src/storage/manifest_manager.py` | Job snapshots, version locking |
| Cleanup mechanism | `src/storage/cleaner.py` | Reference counting, trash management |
| Design documentation | `docs/04-storage-system-design.md` | Full system design v4.4 |
| Unit tests | `tests/` | pytest, 64 tests |

## CODE MAP

| Symbol | Type | Location | Role |
|--------|------|----------|------|
| BlobStorage | class | blob_storage.py | SHA-256 content-addressed storage |
| FileManager | class | file_manager.py | Upload & version management |
| Database | class | database.py | SQLite metadata store |
| HashCache | class | hash_cache.py | Local hash cache with mtime+size |
| ManifestManager | class | manifest_manager.py | Job snapshot management |
| StorageCleaner | class | cleaner.py | Reference counting & blob cleanup |
| compute_sha256 | func | blob_storage.py | File hash calculation |
| normalize_windows_path | func | file_manager.py | Path normalization |
| UploadResult | dataclass | file_manager.py | Upload result container |
| ManifestInfo | dataclass | manifest_manager.py | Manifest snapshot info |

## CONVENTIONS

- **Path normalization**: Windows paths → `D/projects/...` (drive uppercase, no colon, path lowercase)
- **Blob path**: `/blobs/{hash[:2]}/{hash[2:4]}/{hash}` - two-level sharding
- **Atomic writes**: Temp file → verify → rename pattern
- **Dedup**: Same content (same hash) = single blob storage
- **Hash cache**: Cache by (path, mtime, size) → hash, auto-invalidate on change
- **Version locking**: Manifest snapshot locks file versions at job creation time
- **Reference counting**: Active jobs + user directory latest versions

## COMMANDS

```bash
# Run tests
python -m pytest tests/ -v

# Demo CLI - Storage
python demo.py init                    # Initialize storage
python demo.py upload <file> [path]    # Upload file
python demo.py info <path>             # File info
python demo.py history <path>          # Version history
python demo.py list                    # List files
python demo.py stats                   # Storage stats
python demo.py cache-stats             # Cache statistics
python demo.py cache-clear             # Clear cache

# Demo CLI - Manifest (P2)
python demo.py job-create <path>...    # Create snapshot
python demo.py job-info <job_id>       # View snapshot info
python demo.py job-list                # List snapshots

# Demo CLI - Cleanup (P2)
python demo.py cleanup-report          # View cleanup report
python demo.py cleanup --dry-run       # Dry run cleanup
python demo.py cleanup                 # Execute cleanup
```

## NOTES

- **P0 Complete**: Blob storage + file upload + version management
- **P1 Complete**: Hash cache (mtime+size) + user directory links
- **P2 Complete**: Manifest snapshots + reference counting cleanup
- **Database**: SQLite for demo, PostgreSQL schema compatible
- **Test coverage**: 64 unit tests (P0: 29, P1: 18, P2: 17)
