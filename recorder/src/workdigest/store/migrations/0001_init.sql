-- 0001_init: local evidence store for the recorder.
-- Forward-only. Never edit after release; add a new numbered file instead.

CREATE TABLE events (
    event_id        TEXT PRIMARY KEY,           -- sha256 of the natural key
    schema_version  INTEGER NOT NULL,
    source          TEXT NOT NULL CHECK (source IN ('claude_code','cursor','git','jira','bitbucket','manual')),
    origin          TEXT NOT NULL CHECK (origin IN ('hook','transcript','poller','api','manual')),
    kind            TEXT NOT NULL,
    ts              TEXT NOT NULL,              -- ISO-8601 with offset
    ts_approx       INTEGER NOT NULL DEFAULT 0, -- 1 when inferred (e.g. Cursor transcripts)
    work_date       TEXT NOT NULL,              -- YYYY-MM-DD
    session_key     TEXT,
    cwd             TEXT,
    repo            TEXT,
    branch          TEXT,
    ticket_keys     TEXT NOT NULL DEFAULT '[]', -- JSON array
    payload         TEXT NOT NULL DEFAULT '{}', -- JSON, redacted
    redaction_rules TEXT NOT NULL DEFAULT '[]', -- JSON array of rule names that fired
    ingested_at     TEXT NOT NULL
);
CREATE INDEX idx_events_work_date ON events (work_date);
CREATE INDEX idx_events_session ON events (session_key, ts);
CREATE INDEX idx_events_repo ON events (repo, work_date);

CREATE TABLE sessions (
    session_key       TEXT PRIMARY KEY,         -- "claude:<id>" | "cursor:<id>"
    source            TEXT NOT NULL,
    first_ts          TEXT NOT NULL,
    last_ts           TEXT NOT NULL,
    cwd               TEXT,
    repo              TEXT,
    branch            TEXT,
    transcript_path   TEXT,
    ended_at          TEXT,
    end_reason        TEXT,
    title             TEXT
);

CREATE TABLE repos (
    name        TEXT PRIMARY KEY,
    path        TEXT NOT NULL UNIQUE,
    remote_url  TEXT,
    web_url     TEXT,                           -- e.g. https://bitbucket.org/ws/repo
    last_polled TEXT
);

CREATE TABLE commits (
    repo          TEXT NOT NULL,
    sha           TEXT NOT NULL,
    author_name   TEXT NOT NULL,
    author_email  TEXT NOT NULL,
    authored_at   TEXT NOT NULL,
    committed_at  TEXT NOT NULL,
    work_date     TEXT NOT NULL,
    subject       TEXT NOT NULL,
    branches      TEXT NOT NULL DEFAULT '[]',   -- JSON array of local branch names
    pushed        INTEGER NOT NULL DEFAULT 0,
    files_changed INTEGER NOT NULL DEFAULT 0,
    insertions    INTEGER NOT NULL DEFAULT 0,
    deletions     INTEGER NOT NULL DEFAULT 0,
    ticket_keys   TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (repo, sha)
);
CREATE INDEX idx_commits_work_date ON commits (work_date);

CREATE TABLE branches (
    repo        TEXT NOT NULL,
    name        TEXT NOT NULL,
    head_sha    TEXT NOT NULL,
    upstream    TEXT,
    created_at  TEXT,                           -- from reflog when available
    first_seen  TEXT NOT NULL,
    baseline    INTEGER NOT NULL DEFAULT 0,     -- 1 = existed before recording started
    PRIMARY KEY (repo, name)
);

CREATE TABLE test_runs (
    test_run_id  TEXT PRIMARY KEY,
    event_id     TEXT NOT NULL REFERENCES events (event_id),
    work_date    TEXT NOT NULL,
    ts           TEXT NOT NULL,
    session_key  TEXT,
    repo         TEXT,
    runner       TEXT NOT NULL,
    command      TEXT NOT NULL,
    passed       INTEGER NOT NULL,
    failed       INTEGER NOT NULL,
    skipped      INTEGER NOT NULL,
    total        INTEGER NOT NULL,
    unit         TEXT NOT NULL DEFAULT 'tests', -- 'tests' | 'packages'
    summary_line TEXT NOT NULL
);
CREATE INDEX idx_test_runs_work_date ON test_runs (work_date);

CREATE TABLE day_state (
    work_date     TEXT PRIMARY KEY,
    closed_at     TEXT,
    rating        INTEGER CHECK (rating IN (1, 3, 7, 9)),
    project       TEXT,
    team_members  TEXT NOT NULL DEFAULT '[]'
);

-- Read positions so ingest is incremental and idempotent.
CREATE TABLE file_offsets (
    path      TEXT PRIMARY KEY,
    offset    INTEGER NOT NULL,
    size      INTEGER NOT NULL,
    mtime_ns  INTEGER NOT NULL,
    state     TEXT NOT NULL DEFAULT '{}'       -- collector-specific JSON
);

-- Transcript tool calls waiting for their result line.
CREATE TABLE pending_tool_uses (
    tool_use_id  TEXT PRIMARY KEY,
    session_key  TEXT NOT NULL,
    data         TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE ingest_errors (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    stage     TEXT NOT NULL,
    error     TEXT NOT NULL,
    ref       TEXT
);
