-- LoongClouds Storage System - PostgreSQL Schema
-- Version: 1.0.0
-- Compatible with design document v4.4

-- 路径规范化辅助函数
CREATE OR REPLACE FUNCTION normalize_path(path TEXT) RETURNS TEXT AS $$
DECLARE
    clean_path TEXT;
    prefix_len INT;
BEGIN
    -- 将反斜杠替换为正斜杠
    clean_path := REPLACE(path, '\', '/');

    -- UNC 路径优先处理（//server/share/...）
    IF clean_path LIKE '//%' THEN
        prefix_len := LENGTH(REGEXP_REPLACE(clean_path, '(^//[^/]+/[^/]+/).*', '\1'));

        IF prefix_len > 0 AND prefix_len < LENGTH(clean_path) THEN
            RETURN SUBSTRING(clean_path FROM 1 FOR prefix_len)
                   || LOWER(SUBSTRING(clean_path FROM prefix_len + 1));
        ELSE
            RETURN clean_path;
        END IF;
    ELSE
        -- 非 UNC 路径：合并连续斜杠
        clean_path := REGEXP_REPLACE(clean_path, '(/)(/+)', '\1', 'g');

        -- 处理本地路径（D:\...）
        IF LENGTH(clean_path) >= 2 AND SUBSTRING(clean_path FROM 2 FOR 1) = ':' THEN
            RETURN UPPER(SUBSTRING(clean_path FROM 1 FOR 1))
                   || LOWER(SUBSTRING(clean_path FROM 3));
        ELSE
            RETURN LOWER(clean_path);
        END IF;
    END IF;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- 逻辑文件表
CREATE TABLE IF NOT EXISTS files (
    id          BIGSERIAL PRIMARY KEY,
    user_name   TEXT NOT NULL,
    path        TEXT NOT NULL,
    project     TEXT,
    status      TEXT DEFAULT 'active'
                CHECK (status IN ('active', 'orphaned', 'deleted')),
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ,
    UNIQUE(user_name, path)
);

-- 文件版本表
CREATE TABLE IF NOT EXISTS file_versions (
    id            BIGSERIAL PRIMARY KEY,
    file_id       BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    version_num   INT NOT NULL,
    hash          CHAR(64) NOT NULL,
    size          BIGINT,
    uploaded_by   TEXT,
    uploaded_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(file_id, version_num)
);

-- 快照表（Manifest）
CREATE TABLE IF NOT EXISTS manifests (
    id            BIGSERIAL PRIMARY KEY,
    job_id        TEXT NOT NULL UNIQUE,
    user_name     TEXT,
    scene_path    TEXT,
    output_path   TEXT,
    drives        TEXT[],
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ,
    archived_at   TIMESTAMPTZ,
    status        TEXT DEFAULT 'submitted'
                  CHECK (status IN ('submitted', 'rendering',
                                    'completed', 'failed',
                                    'archiving', 'archived', 'deleted'))
);

-- 快照明细表
CREATE TABLE IF NOT EXISTS manifest_entries (
    id               BIGSERIAL PRIMARY KEY,
    manifest_id      BIGINT NOT NULL REFERENCES manifests(id) ON DELETE CASCADE,
    file_id          BIGINT REFERENCES files(id),
    file_version_id  BIGINT REFERENCES file_versions(id),
    original_path    TEXT NOT NULL,
    hash             CHAR(64) NOT NULL
);

-- 文件移动/重命名别名表
CREATE TABLE IF NOT EXISTS path_aliases (
    id              BIGSERIAL PRIMARY KEY,
    user_name       TEXT NOT NULL,
    old_path        TEXT NOT NULL,
    new_path        TEXT NOT NULL,
    file_id         BIGINT REFERENCES files(id),
    migrated_at     TIMESTAMPTZ DEFAULT NOW(),
    migrated_by     TEXT,
    UNIQUE(user_name, old_path, new_path)
);

-- 渲染节点心跳表
CREATE TABLE IF NOT EXISTS node_heartbeats (
    id          BIGSERIAL PRIMARY KEY,
    node_id     TEXT NOT NULL UNIQUE,
    job_id      TEXT,
    status      TEXT,
    progress    INT DEFAULT 0,
    timestamp   TIMESTAMPTZ DEFAULT NOW()
);

-- 任务失败记录表
CREATE TABLE IF NOT EXISTS task_failures (
    id          BIGSERIAL PRIMARY KEY,
    job_id      TEXT NOT NULL,
    reason      TEXT,
    details     TEXT,
    failed_at   TIMESTAMPTZ DEFAULT NOW()
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_files_user ON files(user_name);
CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);
CREATE INDEX IF NOT EXISTS idx_file_versions_file_id ON file_versions(file_id);
CREATE INDEX IF NOT EXISTS idx_file_versions_hash ON file_versions(hash);
CREATE INDEX IF NOT EXISTS idx_file_versions_file_version ON file_versions(file_id, version_num DESC);
CREATE INDEX IF NOT EXISTS idx_manifests_user ON manifests(user_name);
CREATE INDEX IF NOT EXISTS idx_manifests_status ON manifests(status);
CREATE INDEX IF NOT EXISTS idx_manifests_job_id ON manifests(job_id);
CREATE INDEX IF NOT EXISTS idx_manifest_entries_manifest ON manifest_entries(manifest_id);
CREATE INDEX IF NOT EXISTS idx_manifest_entries_hash ON manifest_entries(hash);

-- 引用计数视图（用于清理器）
CREATE OR REPLACE VIEW blob_ref_counts AS
SELECT
    v.hash,
    COUNT(DISTINCT v.id) AS user_dir_refs,
    COUNT(DISTINCT me.id) AS manifest_refs,
    COUNT(DISTINCT v.id) + COUNT(DISTINCT me.id) AS total_refs
FROM file_versions v
LEFT JOIN manifest_entries me ON me.hash = v.hash
GROUP BY v.hash;

-- 创建更新时间戳触发器函数
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 应用触发器
CREATE TRIGGER files_updated_at
    BEFORE UPDATE ON files
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER manifests_updated_at
    BEFORE UPDATE ON manifests
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

-- 授权
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO loongclouds;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO loongclouds;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO loongclouds;
