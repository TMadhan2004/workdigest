-- 0004_cloud: local bookkeeping for Supabase sync. Forward-only.

-- Hash of the last pushed version of each cloud row, so only changes are sent.
CREATE TABLE cloud_sync_state (
    table_name  TEXT NOT NULL,
    row_key     TEXT NOT NULL,
    row_hash    TEXT NOT NULL,
    pushed_at   TEXT NOT NULL,
    PRIMARY KEY (table_name, row_key)
);

-- Watermarks such as the last pull time.
CREATE TABLE cloud_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
