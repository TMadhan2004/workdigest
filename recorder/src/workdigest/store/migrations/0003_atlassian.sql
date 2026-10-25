-- 0003_atlassian: read-only facts from Jira Cloud and Bitbucket Cloud.
-- Forward-only.

CREATE TABLE tickets (
    key          TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    status       TEXT,
    status_category TEXT,                 -- 'To Do' | 'In Progress' | 'Done'
    issue_type   TEXT,
    assignee_is_me INTEGER NOT NULL DEFAULT 0,
    due_date     TEXT,
    parent_key   TEXT,
    url          TEXT NOT NULL,
    updated_at   TEXT,
    synced_at    TEXT NOT NULL
);

CREATE TABLE ticket_events (
    event_id    TEXT PRIMARY KEY,          -- sha256(key, kind, source id)
    key         TEXT NOT NULL REFERENCES tickets (key),
    kind        TEXT NOT NULL CHECK (kind IN ('comment', 'status_change', 'field_change')),
    ts          TEXT NOT NULL,
    work_date   TEXT NOT NULL,
    by_me       INTEGER NOT NULL,
    detail      TEXT NOT NULL DEFAULT '{}' -- JSON: from/to, field, comment excerpt (redacted)
);
CREATE INDEX idx_ticket_events_day ON ticket_events (work_date, key);

CREATE TABLE pull_requests (
    repo              TEXT NOT NULL,
    pr_id             INTEGER NOT NULL,
    title             TEXT NOT NULL,
    url               TEXT NOT NULL,
    source_branch     TEXT,
    dest_branch       TEXT,
    state             TEXT NOT NULL,       -- OPEN | MERGED | DECLINED | SUPERSEDED
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    first_approved_at TEXT,
    approved_by       TEXT NOT NULL DEFAULT '[]',
    declined_at       TEXT,
    merged_at         TEXT,
    ticket_keys       TEXT NOT NULL DEFAULT '[]',
    synced_at         TEXT NOT NULL,
    PRIMARY KEY (repo, pr_id)
);

CREATE TABLE in_progress_snapshot (
    synced_on  TEXT NOT NULL,              -- work date of the sync
    key        TEXT NOT NULL,
    status     TEXT NOT NULL,
    PRIMARY KEY (synced_on, key)
);

-- Keys the recorder saw that Jira says do not exist or are not visible ("CA-0214 doesn't open").
CREATE TABLE ticket_missing (
    key        TEXT NOT NULL,
    synced_on  TEXT NOT NULL,
    PRIMARY KEY (key, synced_on)
);
