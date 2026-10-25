"""SQLite evidence store: connection management, migrations and writes."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any

from workdigest.clock import now_local
from workdigest.models import Event, TestRun, canonical_json, stable_hash

_MIGRATIONS_PACKAGE = "workdigest.store.migrations"


@dataclass(frozen=True, slots=True)
class FileOffset:
    """Where the last ingest stopped reading a file."""

    offset: int
    size: int
    mtime_ns: int
    state: dict[str, Any]


class Store:
    """Thin repository over the local SQLite database."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Wrap an open connection (use :meth:`open`)."""
        self.conn = conn

    # ------------------------------------------------------------ lifecycle
    @classmethod
    def open(cls, path: Path | str) -> Store:
        """Open (and migrate) the database at ``path``; ``":memory:"`` is allowed."""
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        store = cls(conn)
        store.migrate()
        if isinstance(path, Path):
            path.chmod(0o600)
        return store

    def close(self) -> None:
        """Close the connection."""
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Run the enclosed writes atomically."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def migrate(self) -> list[str]:
        """Apply pending forward-only migrations; return the names applied."""
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        done = {row["name"] for row in self.conn.execute("SELECT name FROM schema_migrations")}
        applied: list[str] = []
        scripts = sorted(
            (
                entry
                for entry in resources.files(_MIGRATIONS_PACKAGE).iterdir()
                if entry.name.endswith(".sql")
            ),
            key=lambda entry: entry.name,
        )
        for script in scripts:
            if script.name in done:
                continue
            sql = script.read_text(encoding="utf-8")
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in _split_sql(sql):
                    self.conn.execute(statement)
                self.conn.execute(
                    "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                    (script.name, now_local().isoformat()),
                )
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            self.conn.execute("COMMIT")
            applied.append(script.name)
        return applied

    # --------------------------------------------------------------- events
    def insert_event(self, event: Event) -> bool:
        """Insert ``event`` unless an event with the same id exists. Return True if new."""
        cursor = self.conn.execute(
            """
            INSERT OR IGNORE INTO events (
                event_id, schema_version, source, origin, kind, ts, ts_approx, work_date,
                session_key, cwd, repo, branch, ticket_keys, payload, redaction_rules, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.schema_version,
                event.source.value,
                event.origin.value,
                event.kind.value,
                event.ts.isoformat(),
                int(event.ts_approx),
                event.work_date,
                event.session_key,
                event.cwd,
                event.repo,
                event.branch,
                canonical_json(sorted(set(event.ticket_keys))),
                canonical_json(event.payload),
                canonical_json(sorted(set(event.redaction_rules))),
                now_local().isoformat(),
            ),
        )
        inserted = cursor.rowcount == 1
        if inserted and event.session_key:
            self._touch_session(event)
        return inserted

    def _touch_session(self, event: Event) -> None:
        ts = event.ts.isoformat()
        self.conn.execute(
            """
            INSERT INTO sessions (session_key, source, first_ts, last_ts, cwd, repo, branch)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (session_key) DO UPDATE SET
                first_ts = MIN(first_ts, excluded.first_ts),
                last_ts  = MAX(last_ts, excluded.last_ts),
                cwd      = COALESCE(sessions.cwd, excluded.cwd),
                repo     = COALESCE(sessions.repo, excluded.repo),
                branch   = COALESCE(excluded.branch, sessions.branch)
            """,
            (event.session_key, event.source.value, ts, ts, event.cwd, event.repo, event.branch),
        )
        transcript = event.payload.get("transcript_path")
        if isinstance(transcript, str) and transcript:
            self.conn.execute(
                "UPDATE sessions SET transcript_path = ? WHERE session_key = ?",
                (transcript, event.session_key),
            )
        title = event.payload.get("title")
        if isinstance(title, str) and title:
            self.conn.execute(
                "UPDATE sessions SET title = COALESCE(title, ?) WHERE session_key = ?",
                (title, event.session_key),
            )
        if event.kind.value == "session_end":
            self.conn.execute(
                "UPDATE sessions SET ended_at = ?, end_reason = ? WHERE session_key = ?",
                (ts, event.payload.get("reason"), event.session_key),
            )

    def insert_test_run(self, event: Event, run: TestRun, command: str) -> bool:
        """Record a parsed test result linked to its source event."""
        # Cursor can report one shell run through two hooks, so the id ignores event_id.
        minute = event.ts.replace(second=0, microsecond=0).isoformat()
        test_run_id = stable_hash(event.session_key or "", command, run.summary_line, minute)
        cursor = self.conn.execute(
            """
            INSERT OR IGNORE INTO test_runs (
                test_run_id, event_id, work_date, ts, session_key, repo, runner, command,
                passed, failed, skipped, total, unit, summary_line
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                test_run_id,
                event.event_id,
                event.work_date,
                event.ts.isoformat(),
                event.session_key,
                event.repo,
                run.runner,
                command,
                run.passed,
                run.failed,
                run.skipped,
                run.total,
                run.unit,
                run.summary_line,
            ),
        )
        return cursor.rowcount == 1

    # -------------------------------------------------------------- offsets
    def get_offset(self, path: str) -> FileOffset | None:
        """Return the stored read position for ``path``."""
        row = self.conn.execute(
            "SELECT offset, size, mtime_ns, state FROM file_offsets WHERE path = ?", (path,)
        ).fetchone()
        if row is None:
            return None
        return FileOffset(row["offset"], row["size"], row["mtime_ns"], json.loads(row["state"]))

    def set_offset(self, path: str, offset: FileOffset) -> None:
        """Persist the read position for ``path``."""
        self.conn.execute(
            """
            INSERT INTO file_offsets (path, offset, size, mtime_ns, state) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (path) DO UPDATE SET
                offset = excluded.offset, size = excluded.size,
                mtime_ns = excluded.mtime_ns, state = excluded.state
            """,
            (path, offset.offset, offset.size, offset.mtime_ns, canonical_json(offset.state)),
        )

    # ---------------------------------------------------- pending tool uses
    def put_pending_tool_use(
        self, tool_use_id: str, session_key: str, data: dict[str, Any]
    ) -> None:
        """Remember a transcript tool call until its result line is read."""
        self.conn.execute(
            "INSERT OR REPLACE INTO pending_tool_uses VALUES (?, ?, ?, ?)",
            (tool_use_id, session_key, canonical_json(data), now_local().isoformat()),
        )

    def pop_pending_tool_use(self, tool_use_id: str) -> dict[str, Any] | None:
        """Return and forget a pending transcript tool call."""
        row = self.conn.execute(
            "SELECT data FROM pending_tool_uses WHERE tool_use_id = ?", (tool_use_id,)
        ).fetchone()
        if row is None:
            return None
        self.conn.execute("DELETE FROM pending_tool_uses WHERE tool_use_id = ?", (tool_use_id,))
        data: dict[str, Any] = json.loads(row["data"])
        return data

    # ------------------------------------------------------------------ git
    def upsert_repo(
        self, name: str, path: str, remote_url: str | None, web_url: str | None
    ) -> None:
        """Record a watched repository and its remote."""
        self.conn.execute(
            """
            INSERT INTO repos (name, path, remote_url, web_url, last_polled) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (name) DO UPDATE SET
                path = excluded.path, remote_url = excluded.remote_url,
                web_url = excluded.web_url, last_polled = excluded.last_polled
            """,
            (name, path, remote_url, web_url, now_local().isoformat()),
        )

    def upsert_commit(self, row: dict[str, Any]) -> bool:
        """Insert or refresh a commit (branches and pushed state change over time)."""
        existed = self.conn.execute(
            "SELECT 1 FROM commits WHERE repo = ? AND sha = ?", (row["repo"], row["sha"])
        ).fetchone()
        self.conn.execute(
            """
            INSERT INTO commits (
                repo, sha, author_name, author_email, authored_at, committed_at, work_date,
                subject, branches, pushed, files_changed, insertions, deletions, ticket_keys
            ) VALUES (
                :repo, :sha, :author_name, :author_email, :authored_at, :committed_at, :work_date,
                :subject, :branches, :pushed, :files_changed, :insertions, :deletions, :ticket_keys
            )
            ON CONFLICT (repo, sha) DO UPDATE SET
                branches = excluded.branches, pushed = excluded.pushed
            """,
            row,
        )
        return existed is None

    def known_branches(self, repo: str) -> dict[str, str]:
        """Return ``{branch: head_sha}`` previously seen for ``repo``."""
        rows = self.conn.execute("SELECT name, head_sha FROM branches WHERE repo = ?", (repo,))
        return {row["name"]: row["head_sha"] for row in rows}

    def upsert_branch(
        self,
        repo: str,
        name: str,
        head_sha: str,
        upstream: str | None,
        created_at: str | None,
        *,
        baseline: bool,
    ) -> None:
        """Insert a branch or update its head."""
        self.conn.execute(
            """
            INSERT INTO branches (repo, name, head_sha, upstream, created_at, first_seen, baseline)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (repo, name) DO UPDATE SET
                head_sha = excluded.head_sha, upstream = excluded.upstream
            """,
            (repo, name, head_sha, upstream, created_at, now_local().isoformat(), int(baseline)),
        )

    # --------------------------------------------------------------- errors
    def log_error(self, stage: str, error: str, ref: str | None = None) -> None:
        """Record a non-fatal ingest problem for `digest doctor`."""
        self.conn.execute(
            "INSERT INTO ingest_errors (ts, stage, error, ref) VALUES (?, ?, ?, ?)",
            (now_local().isoformat(), stage, error[:2000], ref),
        )

    # -------------------------------------------------------------- queries
    def query(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> list[sqlite3.Row]:
        """Run a read query."""
        return list(self.conn.execute(sql, params))

    def scalar(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        """Run a read query that returns one value."""
        row = self.conn.execute(sql, params).fetchone()
        return None if row is None else row[0]


def _split_sql(script: str) -> list[str]:
    """Split a migration into statements (no triggers or procedures are used)."""
    lines = [line for line in script.splitlines() if not line.strip().startswith("--")]
    return [stmt.strip() for stmt in "\n".join(lines).split(";") if stmt.strip()]


def iso(ts: datetime) -> str:
    """Format a datetime for storage."""
    return ts.isoformat()
