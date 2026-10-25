-- 0006_ask_cleared: asks are answered Done (evidence) or Disagree (reason); "Clear open asks"
-- sets them to "cleared" (never reported). "waiting" is retired: existing ones become open.
-- SQLite cannot change a CHECK constraint in place, so the table is rebuilt. Forward-only.

CREATE TABLE asks_new (
    ask_id             TEXT PRIMARY KEY,
    source_item_id     TEXT REFERENCES reply_items (item_id),
    text               TEXT NOT NULL,
    kind               TEXT NOT NULL CHECK (kind IN ('one_time_action','question')),
    related_tickets    TEXT NOT NULL DEFAULT '[]',
    status             TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done','disagree','waiting','cleared')),
    needs_real_action  INTEGER NOT NULL DEFAULT 0,
    evidence           TEXT,
    reason             TEXT,
    opened_on          TEXT NOT NULL,
    closed_on          TEXT,
    times_raised       INTEGER NOT NULL DEFAULT 1
);
INSERT INTO asks_new
SELECT ask_id, source_item_id, text, kind, related_tickets,
       CASE status WHEN 'waiting' THEN 'open' ELSE status END,
       needs_real_action, evidence, reason, opened_on, closed_on, times_raised
FROM asks;
DROP TABLE asks;
ALTER TABLE asks_new RENAME TO asks;
