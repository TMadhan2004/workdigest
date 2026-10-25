-- 0005_hidden_rules: sessions hidden in the app and the user's own rules (both mirrored from
-- the cloud on every worker tick). Forward-only.

-- Sessions the user hid from digests (personal work). The data stays; it is only skipped.
CREATE TABLE hidden_sessions (
    session_key TEXT PRIMARY KEY,
    hidden_at   TEXT NOT NULL
);

-- Rules the user typed ("my rules"), with what the Mac understood them to mean.
CREATE TABLE my_rules (
    rule_id    TEXT PRIMARY KEY,
    text       TEXT NOT NULL,
    status     TEXT NOT NULL CHECK (status IN ('parsing', 'needs_review', 'active', 'off')),
    params     TEXT NOT NULL DEFAULT '{}',   -- JSON RuleFilters
    updated_at TEXT NOT NULL
);
