# Architecture

## The whole system

```
Teammate's Mac                                     Supabase (one shared project)          Teammate's phone
──────────────                                     ────────────────────────────           ────────────────
install.sh ─► uv tool install ─► digest setup      auth: @ei.study sign-ups only          Android app (Flutter)
                 (wizard on 127.0.0.1)  ─────────► settings, asks, rules, digests,  ◄────► Today · Draft · Asks
launchd ─► digest daemon                           session summaries, jobs, devices        Timeline · History
   ├─ hooks/transcripts/git ─► SQLite (redacted)   row-level security on every row         Update banner ◄─┐
   ├─ Jira + Bitbucket (read-only tokens)          realtime publication ─────────────────►  live refresh    │
   ├─ cloud worker: push, pull, jobs, heartbeat ─► pg_cron queues the 20:00 draft                           │
   ├─ drafting: evidence → your AI → grounding → checks                                                     │
   ├─ AI health (hourly) ─► app notification + Mac notification                                             │
   └─ Mac update check (daily) ◄───────────┐                                                               │
                                            │                                                               │
GitHub Actions (manual Release) ─► GitHub Release (APK, wheel, install.sh, version.json)                     │
                                 └► Vercel: website + install.sh + version.json ────────────────────────────┘
```

- **Onboarding** creates the account on the Mac: the domain is checked in the wizard and enforced in the database. The phone only signs in.
- **Per-person format.** The template, manager name, header lines and sign-off are learned from the uploaded conversation (`digest/onboarding.py`). The generator writes the template's sections, built-in or custom.
- **Updates.** `version.json` drives both sides. Phones check on open, on resume and every 6 hours (WorkManager), and verify the APK's SHA-256 before installing. Macs check daily and are told to run `digest update`.

The decisions are in [ADR 0009](adr/0009-team-rollout.md). Releasing is described in [release.md](release.md).

## Capture data flow (on the Mac)

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
