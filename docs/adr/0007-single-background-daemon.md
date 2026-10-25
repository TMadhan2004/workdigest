# ADR 0007: One background daemon under launchd

**Status:** accepted (24 Oct 2026)

**Context.** Phase 6 needs recurring work: ingest the hook spool, poll git and transcripts, run the cloud worker, summarize sessions hourly, draft at 20:00 and remind before the 21:00 deadline. It must start at login, survive crashes, and never get in the way of Claude Code or Cursor sessions.

**Decision.**
- **One process (`digest daemon`) runs every task on its own interval.** Separate launchd agents (recorder, worker, summarizer) would mean several writers to one SQLite file and three things to install and debug. Tasks are isolated: an exception is logged and the next task still runs.
- **A per-user launchd agent** (`~/Library/LaunchAgents/com.workdigest.daemon.plist`) with `RunAtLoad`, `KeepAlive`, a 30 s restart throttle, `ProcessType=Background` and low-priority I/O. There is no root and no system-wide change, and `digest service uninstall` removes it.
- **The 20:00 trigger stays in Supabase when cloud sync is on** (pg_cron queues the job and the app sees it). Without cloud the daemon drafts locally at `digest.nudge_time`. Once-per-day markers live in SQLite, so a restart does not repeat a nudge.
- **Desktop notifications via `osascript`** need no extra app or entitlement. The text comes from the worker's notification, so the Mac and the app say the same thing.
- **Time is injected** (`clock`), so the schedule, the evening draft and the reminder are tested without waiting or sleeping.

**Consequences.** One slow task (the hourly summary pass) delays the others by its duration. That is acceptable for jobs measured in minutes. A threaded scheduler would add locking for little gain.
