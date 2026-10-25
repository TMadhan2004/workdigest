-- 0002_digest: digest engine (digests, manager replies, asks, standing rules, summaries, plans).
-- Forward-only.

CREATE TABLE digests (
    digest_id        TEXT PRIMARY KEY,
    work_date        TEXT NOT NULL,
    origin           TEXT NOT NULL CHECK (origin IN ('imported','generated','edited')),
    status           TEXT NOT NULL CHECK (status IN ('draft','final','sent_marked')),
    template_version TEXT,
    text             TEXT NOT NULL,              -- WhatsApp text
    structure        TEXT NOT NULL DEFAULT '{}', -- JSON: parsed or generated sections
    gaps             TEXT NOT NULL DEFAULT '[]', -- JSON list of gap objects
    created_at       TEXT NOT NULL
);
CREATE INDEX idx_digests_date ON digests (work_date, created_at);

CREATE TABLE digest_statements (
    digest_id     TEXT NOT NULL REFERENCES digests (digest_id) ON DELETE CASCADE,
    section_key   TEXT NOT NULL,
    idx           INTEGER NOT NULL,
    text          TEXT NOT NULL,
    verified      INTEGER NOT NULL DEFAULT 0,
    evidence_ids  TEXT NOT NULL DEFAULT '[]',
    problems      TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (digest_id, section_key, idx)
);

CREATE TABLE manager_replies (
    reply_id       TEXT PRIMARY KEY,           -- sha256 of normalized text
    for_work_date  TEXT,                       -- the digest it replies to
    raw_text       TEXT NOT NULL,
    received_at    TEXT NOT NULL
);

CREATE TABLE reply_items (
    item_id         TEXT PRIMARY KEY,
    reply_id        TEXT NOT NULL REFERENCES manager_replies (reply_id) ON DELETE CASCADE,
    idx             INTEGER NOT NULL,
    type            TEXT NOT NULL CHECK (type IN ('standing_rule','one_time_action','question','feedback','info_only')),
    heading         TEXT,
    text            TEXT NOT NULL,
    related_tickets TEXT NOT NULL DEFAULT '[]',
    due_date        TEXT,
    status          TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','confirmed','dismissed')),
    method          TEXT NOT NULL DEFAULT 'heuristic' -- 'heuristic' | 'llm' | 'manual'
);

CREATE TABLE asks (
    ask_id             TEXT PRIMARY KEY,
    source_item_id     TEXT REFERENCES reply_items (item_id),
    text               TEXT NOT NULL,
    kind               TEXT NOT NULL CHECK (kind IN ('one_time_action','question')),
    related_tickets    TEXT NOT NULL DEFAULT '[]',
    status             TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done','disagree','waiting')),
    needs_real_action  INTEGER NOT NULL DEFAULT 0,
    evidence           TEXT,                    -- note or link proving done
    reason             TEXT,                    -- why disagree / waiting on whom
    opened_on          TEXT NOT NULL,
    closed_on          TEXT,
    times_raised       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE standing_rules (
    rule_id      TEXT PRIMARY KEY,
    text         TEXT NOT NULL,
    check_kind   TEXT NOT NULL CHECK (check_kind IN ('section_present','header_present','regex','risk_owner_date','named_tests','plan_dates','manual')),
    check_params TEXT NOT NULL DEFAULT '{}',
    source       TEXT NOT NULL DEFAULT 'seed',  -- 'seed' | reply item id
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL
);

CREATE TABLE session_summaries (
    session_key  TEXT NOT NULL,
    work_date    TEXT NOT NULL,
    input_hash   TEXT NOT NULL,
    method       TEXT NOT NULL,                 -- provider name or 'deterministic'
    summary      TEXT NOT NULL,                 -- JSON (see digest/summarizer.py)
    created_at   TEXT NOT NULL,
    PRIMARY KEY (session_key, work_date)
);

CREATE TABLE plans (
    plan_date   TEXT PRIMARY KEY,               -- date of the digest that set the plan
    horizon     INTEGER NOT NULL DEFAULT 5,
    items       TEXT NOT NULL                   -- JSON list of strings
);
