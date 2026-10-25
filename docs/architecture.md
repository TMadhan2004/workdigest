# Architecture

## Phase 1 data flow (on the Mac only)

```
Claude Code ──hook (async)──┐
Cursor ───────hook──────────┤   workdigest-hook.py (stdlib, always exit 0)
                            └──► spool/YYYY-MM-DD.jsonl  (append + flock, 0600)
                                          │
digest run / ingest  ───────────────────► │ spool ingest (offset-tracked, complete lines only)
  ├─ ~/.claude/projects/**.jsonl ───────► │ Claude transcript backfill (same natural keys)
  ├─ ~/.cursor/projects/*/agent-transcripts ► Cursor transcript backfill (best effort)
  └─ git repos in config.toml ──────────► │ git poller (my commits, branches, pushed, diffstat)
                                          ▼
                         EventFactory: detect source → redact → attach repo/branch/tickets
                                          ▼
                  SQLite (WAL): events · sessions · commits · branches · test_runs
                                          ▼
                                 digest today [--json]
```

## Key properties

- **Never disturb live sessions.** Hooks only append a line. All parsing happens later in the daemon.
- **Idempotent.** Every capture path derives the same `event_id` for the same fact: `tool_use_id`, normalized prompt text, commit SHA, and so on. The store uses `INSERT OR IGNORE`.
- **Evidence over claims.** Test counts are parsed from real runner output, with the summary line kept. Commits come from git.
- **Redaction first.** Payload text is redacted before truncation and before storage.
- **Stage isolation.** A failing collector is logged to `ingest_errors`, and the other stages still run.

## Event schema

The event schema is defined in `recorder/src/workdigest/models.py` (`Event`). The table definitions are in `recorder/src/workdigest/store/migrations/0001_init.sql`.

## Decisions

See [adr/](adr/).
