"""Assemble one work day's evidence from the store (read-only)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict

from workdigest.clock import parse_iso
from workdigest.store.db import Store


class SessionSummary(BaseModel):
    """One agent session as seen on a day."""

    model_config = ConfigDict(frozen=True)

    session_key: str
    source: str
    title: str | None
    repos: list[str]
    first_ts: str
    last_ts: str
    status: str  # active | idle | ended
    prompts: int
    tool_uses: int
    shell_runs: int
    file_edits: int
    files_touched: list[str]
    tickets: list[str]


class CommitRow(BaseModel):
    """A commit of mine on that day."""

    model_config = ConfigDict(frozen=True)

    repo: str
    sha: str
    subject: str
    authored_at: str
    branches: list[str]
    pushed: bool
    files_changed: int
    insertions: int
    deletions: int
    tickets: list[str]


class TestRunRow(BaseModel):
    """A parsed test result."""

    model_config = ConfigDict(frozen=True)
    __test__ = False

    ts: str
    repo: str | None
    session_key: str | None
    runner: str
    passed: int
    failed: int
    skipped: int
    total: int
    unit: str
    command: str
    summary_line: str


class DayEvidence(BaseModel):
    """Everything captured for one work day."""

    model_config = ConfigDict(frozen=True)

    work_date: str
    sessions: list[SessionSummary]
    commits: list[CommitRow]
    test_runs: list[TestRunRow]
    branches_created: list[dict[str, Any]]
    tickets: list[str]
    repos: list[str]
    counts: dict[str, int]
    ingest_errors: int


def _status(row: Any, now: datetime, idle_after: timedelta) -> str:
    if row["ended_at"]:
        return "ended"
    last = parse_iso(row["last_ts"])
    if last is not None and now - last > idle_after:
        return "idle"
    return "active"


def day_evidence(
    store: Store, work_date: str, now: datetime, idle_timeout_minutes: int
) -> DayEvidence:
    """Collect the evidence for ``work_date`` (``YYYY-MM-DD``)."""
    idle_after = timedelta(minutes=idle_timeout_minutes)
    session_rows = store.query(
        """
        SELECT s.*, MIN(e.ts) AS day_first, MAX(e.ts) AS day_last
        FROM sessions s JOIN events e ON e.session_key = s.session_key
        WHERE e.work_date = ? AND s.session_key NOT IN (SELECT session_key FROM hidden_sessions)
        GROUP BY s.session_key ORDER BY day_first
        """,
        (work_date,),
    )
    sessions: list[SessionSummary] = []
    all_tickets: dict[str, None] = {}
    for row in session_rows:
        key = row["session_key"]
        kinds = {
            r["kind"]: r["n"]
            for r in store.query(
                "SELECT kind, COUNT(*) AS n FROM events "
                "WHERE session_key = ? AND work_date = ? GROUP BY kind",
                (key, work_date),
            )
        }
        repos = [
            r["repo"]
            for r in store.query(
                "SELECT repo FROM events "
                "WHERE session_key = ? AND work_date = ? AND repo IS NOT NULL "
                "GROUP BY repo ORDER BY COUNT(*) DESC",
                (key, work_date),
            )
        ]
        files = sorted(
            {
                json.loads(r["payload"]).get("file_path")
                for r in store.query(
                    "SELECT payload FROM events WHERE session_key = ? AND work_date = ? "
                    "AND (kind = 'file_edit' OR json_extract(payload, '$.is_edit') = 1)",
                    (key, work_date),
                )
            }
            - {None}
        )
        tickets = _tickets(
            store,
            "SELECT ticket_keys FROM events WHERE session_key = ? AND work_date = ?",
            (key, work_date),
        )
        all_tickets.update(dict.fromkeys(tickets))
        edit_tools = store.scalar(
            "SELECT COUNT(*) FROM events WHERE session_key = ? AND work_date = ? "
            "AND json_extract(payload, '$.is_edit') = 1",
            (key, work_date),
        )
        sessions.append(
            SessionSummary(
                session_key=key,
                source=row["source"],
                title=row["title"],
                repos=repos,
                first_ts=row["day_first"],
                last_ts=row["day_last"],
                status=_status(row, now, idle_after),
                prompts=kinds.get("prompt", 0),
                tool_uses=kinds.get("tool_use", 0),
                shell_runs=kinds.get("shell", 0),
                file_edits=kinds.get("file_edit", 0) + int(edit_tools or 0),
                files_touched=files,
                tickets=tickets,
            )
        )

    commits = [
        CommitRow(
            repo=r["repo"],
            sha=r["sha"],
            subject=r["subject"],
            authored_at=r["authored_at"],
            branches=json.loads(r["branches"]),
            pushed=bool(r["pushed"]),
            files_changed=r["files_changed"],
            insertions=r["insertions"],
            deletions=r["deletions"],
            tickets=json.loads(r["ticket_keys"]),
        )
        for r in store.query(
            "SELECT * FROM commits WHERE work_date = ? ORDER BY repo, authored_at", (work_date,)
        )
    ]
    commits = dedupe_rebased(commits)
    for commit in commits:
        all_tickets.update(dict.fromkeys(commit.tickets))

    test_runs = [
        TestRunRow(
            ts=r["ts"],
            repo=r["repo"],
            session_key=r["session_key"],
            runner=r["runner"],
            passed=r["passed"],
            failed=r["failed"],
            skipped=r["skipped"],
            total=r["total"],
            unit=r["unit"],
            command=r["command"],
            summary_line=r["summary_line"],
        )
        for r in store.query(
            "SELECT * FROM test_runs WHERE work_date = ? AND (session_key IS NULL OR "
            "session_key NOT IN (SELECT session_key FROM hidden_sessions)) ORDER BY ts",
            (work_date,),
        )
    ]
    branch_rows = store.query(
        "SELECT repo, ts, payload, ticket_keys FROM events "
        "WHERE work_date = ? AND kind = 'branch_created' ORDER BY ts",
        (work_date,),
    )
    branches = [
        {
            "repo": r["repo"],
            "ts": r["ts"],
            "tickets": json.loads(r["ticket_keys"]),
            **json.loads(r["payload"]),
        }
        for r in branch_rows
    ]
    for branch in branches:
        all_tickets.update(dict.fromkeys(branch["tickets"]))
    counts = {
        r["source"]: r["n"]
        for r in store.query(
            "SELECT source, COUNT(*) AS n FROM events WHERE work_date = ? GROUP BY source",
            (work_date,),
        )
    }
    repos = sorted({repo for s in sessions for repo in s.repos} | {c.repo for c in commits})
    errors = int(
        store.scalar("SELECT COUNT(*) FROM ingest_errors WHERE substr(ts, 1, 10) = ?", (work_date,))
        or 0
    )
    return DayEvidence(
        work_date=work_date,
        sessions=sessions,
        commits=commits,
        test_runs=test_runs,
        branches_created=branches,
        tickets=list(all_tickets),
        repos=repos,
        counts=counts,
        ingest_errors=errors,
    )


def _tickets(store: Store, sql: str, params: tuple[Any, ...]) -> list[str]:
    seen: dict[str, None] = {}
    for row in store.query(sql, params):
        seen.update(dict.fromkeys(json.loads(row["ticket_keys"])))
    return list(seen)


BACKUP_PREFIXES = ("backup/", "bak/", "tmp/")


def is_backup_branch(name: str) -> bool:
    """Branches kept only as safety copies (``backup/eit-9286-pre-rebase``)."""
    return name.startswith(BACKUP_PREFIXES)


def dedupe_rebased(commits: list[CommitRow]) -> list[CommitRow]:
    """Collapse rebased or cherry-picked copies of the same commit (same repo, subject and author time).

    A rebase gives each commit a new SHA but keeps its author date, so before-and-after copies
    would otherwise be counted twice. The copy on a non-backup branch is kept; branches and the
    pushed flag are merged across copies.
    """
    groups: dict[tuple[str, str, str], list[CommitRow]] = {}
    for commit in commits:
        groups.setdefault((commit.repo, commit.subject, commit.authored_at), []).append(commit)
    out: list[CommitRow] = []
    for copies in groups.values():

        def rank(c: CommitRow) -> tuple[int, int]:
            live = [b for b in c.branches if not is_backup_branch(b)]
            return (0 if live else 1, -len(live))

        best = sorted(copies, key=rank)[0]
        branches = sorted(
            {b for c in copies for b in c.branches}, key=lambda b: (is_backup_branch(b), b)
        )
        out.append(
            best.model_copy(update={"branches": branches, "pushed": any(c.pushed for c in copies)})
        )
    return sorted(out, key=lambda c: (c.repo, c.authored_at))
