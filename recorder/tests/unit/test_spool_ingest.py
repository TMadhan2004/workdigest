"""Spool to SQLite ingest: dedupe, sessions, partial lines, bad records."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from tests.conftest import IST, claude_payload, cursor_payload, spool_line
from workdigest.config import Config
from workdigest.ingest.normalize import EventFactory
from workdigest.ingest.spool import ingest_spool
from workdigest.paths import Paths
from workdigest.pipeline import build_factory
from workdigest.report.today import day_evidence
from workdigest.store.db import Store

T0 = datetime(2026, 10, 23, 10, 0, tzinfo=IST)


def _count(store: Store, sql: str = "SELECT COUNT(*) FROM events", *params: object) -> int:
    return int(store.scalar(sql, tuple(params)))


def test_ingest_is_idempotent(
    store: Store, factory: EventFactory, paths: Paths, write_spool: Callable[..., Path]
) -> None:
    """TC-ING-001: Re-running ingest over the same spool adds nothing.

    Given: a spool file with 3 distinct events.
    When: ingest runs twice, and a third time after the stored offset is reset.
    Then: the store holds exactly 3 events each time.
    """
    write_spool(
        [
            spool_line(claude_payload("SessionStart", source="startup"), T0),
            spool_line(
                claude_payload("UserPromptSubmit", prompt="Fix EIT-8403 tests"),
                T0 + timedelta(seconds=5),
            ),
            spool_line(
                claude_payload("Stop", last_assistant_message="Done."), T0 + timedelta(minutes=2)
            ),
        ]
    )
    first = ingest_spool(store, factory, paths.spool_dir, today=date(2026, 10, 23))
    second = ingest_spool(store, factory, paths.spool_dir, today=date(2026, 10, 23))
    assert (first.inserted, second.lines) == (3, 0)
    store.conn.execute("DELETE FROM file_offsets")
    third = ingest_spool(store, factory, paths.spool_dir, today=date(2026, 10, 23))
    assert third.duplicates == 3
    assert _count(store) == 3


def test_duplicate_hook_deliveries_collapse(
    store: Store, factory: EventFactory, paths: Paths, write_spool: Callable[..., Path]
) -> None:
    """TC-ING-002: The same hook payload delivered twice is stored once.

    Given: identical PostToolUse payloads (same tool_use_id) received 300 ms apart.
    When: the spool is ingested.
    Then: one tool_use event exists and the duplicate is counted.
    """
    payload = claude_payload(
        "PostToolUse", tool_name="Read", tool_use_id="toolu_dup", tool_input={"file_path": "/x.py"}
    )
    write_spool([spool_line(payload, T0), spool_line(payload, T0 + timedelta(milliseconds=300))])
    stats = ingest_spool(store, factory, paths.spool_dir, today=date(2026, 10, 23))
    assert (stats.inserted, stats.duplicates) == (1, 1)


def test_claude_hook_running_inside_cursor_dedupes_with_native(
    store: Store, factory: EventFactory, paths: Paths, write_spool: Callable[..., Path]
) -> None:
    """TC-ING-003: A Claude Code hook imported by Cursor is attributed to Cursor and deduped.

    Given: Cursor's third-party import runs our Claude hook (source_hint=claude_code) and
           our native Cursor hook for the same postToolUse (same tool_use_id).
    When: both records are ingested.
    Then: one event exists, with source=cursor and session cursor:<conversation_id>.
    """
    payload = cursor_payload(
        "postToolUse", tool_name="Shell", tool_use_id="tu-9", tool_input={"command": "ls"}
    )
    write_spool(
        [spool_line(payload, T0, source="claude_code"), spool_line(payload, T0, source="cursor")]
    )
    ingest_spool(store, factory, paths.spool_dir, today=date(2026, 10, 23))
    rows = store.query("SELECT source, session_key FROM events")
    assert [(r["source"], r["session_key"]) for r in rows] == [("cursor", "cursor:conv-1")]


def test_parallel_sessions_recorded_separately(
    store: Store, factory: EventFactory, paths: Paths, write_spool: Callable[..., Path]
) -> None:
    """TC-ING-004: Interleaved events from parallel sessions stay in separate sessions.

    Given: two Claude Code sessions and one Cursor conversation whose events interleave.
    When: the spool is ingested and the day's evidence is built.
    Then: there are three sessions with their own prompt and tool counts.
    """
    lines = []
    for i in range(6):
        ts = T0 + timedelta(seconds=i * 10)
        lines.append(
            spool_line(claude_payload("UserPromptSubmit", session="A", prompt=f"A prompt {i}"), ts)
        )
        lines.append(
            spool_line(
                claude_payload(
                    "PostToolUse", session="B", tool_name="Read", tool_use_id=f"b{i}", tool_input={}
                ),
                ts,
            )
        )
        if i % 2 == 0:
            lines.append(
                spool_line(
                    cursor_payload(
                        "beforeSubmitPrompt", generation_id=f"g{i}", prompt=f"C prompt {i}"
                    ),
                    ts,
                    source="cursor",
                )
            )
    write_spool(lines)
    ingest_spool(store, factory, paths.spool_dir, today=date(2026, 10, 23))
    evidence = day_evidence(store, "2026-10-23", T0 + timedelta(minutes=5), 30)
    by_key = {s.session_key: s for s in evidence.sessions}
    assert set(by_key) == {"claude:A", "claude:B", "cursor:conv-1"}
    assert (by_key["claude:A"].prompts, by_key["claude:A"].tool_uses) == (6, 0)
    assert (by_key["claude:B"].prompts, by_key["claude:B"].tool_uses) == (0, 6)
    assert by_key["cursor:conv-1"].prompts == 3


def test_missing_stop_and_session_end_marks_idle(
    store: Store, factory: EventFactory, paths: Paths, write_spool: Callable[..., Path]
) -> None:
    """TC-ING-005: Sessions that never sent Stop or SessionEnd are shown as idle, not lost.

    Given: session A has only a prompt (no Stop or SessionEnd); session B ended normally.
    When: evidence is built 10 minutes later, then 45 minutes later.
    Then: A is active at 10 minutes and idle at 45 minutes, and B is always ended.
    """
    write_spool(
        [
            spool_line(claude_payload("UserPromptSubmit", session="A", prompt="start work"), T0),
            spool_line(claude_payload("UserPromptSubmit", session="B", prompt="other"), T0),
            spool_line(
                claude_payload("SessionEnd", session="B", reason="prompt_input_exit"),
                T0 + timedelta(minutes=1),
            ),
        ]
    )
    ingest_spool(store, factory, paths.spool_dir, today=date(2026, 10, 23))
    soon = {
        s.session_key: s.status
        for s in day_evidence(store, "2026-10-23", T0 + timedelta(minutes=10), 30).sessions
    }
    later = {
        s.session_key: s.status
        for s in day_evidence(store, "2026-10-23", T0 + timedelta(minutes=45), 30).sessions
    }
    assert soon == {"claude:A": "active", "claude:B": "ended"}
    assert later == {"claude:A": "idle", "claude:B": "ended"}


def test_partial_trailing_line_waits_for_completion(
    store: Store, factory: EventFactory, paths: Paths, write_spool: Callable[..., Path]
) -> None:
    """TC-ING-006: A line still being written is not consumed until it is complete.

    Given: a spool whose last record has no trailing newline yet.
    When: ingest runs, the writer finishes the line, and ingest runs again.
    Then: the first pass ingests only the complete line, and the second ingests the rest.
    """
    complete = spool_line(claude_payload("Stop", last_assistant_message="one"), T0)
    partial = spool_line(claude_payload("Stop", last_assistant_message="two"), T0)
    path = write_spool([complete, partial[:40]], newline_at_end=False)
    first = ingest_spool(store, factory, paths.spool_dir, today=date(2026, 10, 23))
    with path.open("a") as handle:
        handle.write(partial[40:] + "\n")
    second = ingest_spool(store, factory, paths.spool_dir, today=date(2026, 10, 23))
    assert (first.inserted, second.inserted, first.errors, second.errors) == (1, 1, 0, 0)


def test_bad_lines_are_logged_not_fatal(
    store: Store, factory: EventFactory, paths: Paths, write_spool: Callable[..., Path]
) -> None:
    """TC-ING-007: Corrupt or unknown records become ingest_errors and processing continues.

    Given: a spool with invalid JSON, a raw (non-JSON) hook record, an unknown hook event
           and one valid event.
    When: ingest runs.
    Then: the valid event is stored and the three problems are logged in ingest_errors.
    """
    raw_record = json.dumps(
        {"v": 1, "source_hint": "claude_code", "received_at": T0.isoformat(), "raw": "oops"}
    )
    write_spool(
        [
            "{not json",
            raw_record,
            spool_line(claude_payload("PreCompact"), T0),
            spool_line(claude_payload("Stop", last_assistant_message="ok"), T0),
        ]
    )
    stats = ingest_spool(store, factory, paths.spool_dir, today=date(2026, 10, 23))
    assert stats.inserted == 1
    assert stats.errors + stats.skipped == 3
    assert _count(store, "SELECT COUNT(*) FROM ingest_errors") == 3


def test_late_night_work_belongs_to_previous_day(
    store: Store,
    factory: EventFactory,
    paths: Paths,
    write_spool: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-ING-008: Events before the 04:00 boundary count toward the previous work day.

    Given: a prompt at 01:30 on 24 Oct and one at 04:30 on 24 Oct (IST, machine TZ = IST).
    When: ingested with boundary_hour=4.
    Then: the first is on work day 2026-10-23 and the second on 2026-10-24.
    """
    write_spool(
        [
            spool_line(
                claude_payload("UserPromptSubmit", prompt="late"),
                datetime(2026, 10, 24, 1, 30, tzinfo=IST),
            ),
            spool_line(
                claude_payload("UserPromptSubmit", prompt="early"),
                datetime(2026, 10, 24, 4, 30, tzinfo=IST),
            ),
        ]
    )
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    time.tzset()
    try:
        ingest_spool(store, factory, paths.spool_dir, today=date(2026, 10, 24))
    finally:
        monkeypatch.undo()
        time.tzset()
    days = [r["work_date"] for r in store.query("SELECT work_date FROM events ORDER BY ts")]
    assert days == ["2026-10-23", "2026-10-24"]


def test_old_consumed_spool_files_are_pruned(
    store: Store, factory: EventFactory, paths: Paths, write_spool: Callable[..., Path]
) -> None:
    """TC-ING-009: Fully ingested spool files past retention are deleted (they hold raw data).

    Given: a fully ingested spool file from 2026-10-01 and an unread one from 2026-10-02.
    When: ingest runs on 2026-10-23.
    Then: both are ingested and deleted, and today's file is never deleted.
    """
    old = write_spool(
        [spool_line(claude_payload("Stop", last_assistant_message="a"), T0)],
        name="2026-10-01.jsonl",
    )
    newer = write_spool(
        [spool_line(claude_payload("Stop", last_assistant_message="b"), T0)],
        name="2026-10-02.jsonl",
    )
    current = write_spool(
        [spool_line(claude_payload("Stop", last_assistant_message="c"), T0)],
        name="2026-10-23.jsonl",
    )
    ingest_spool(store, factory, paths.spool_dir, today=date(2026, 10, 23))
    assert not old.exists()
    assert not newer.exists()
    assert current.exists()


def test_test_results_from_hook_output_are_recorded(
    store: Store, factory: EventFactory, paths: Paths, write_spool: Callable[..., Path]
) -> None:
    """TC-ING-010: A test run seen in a Bash hook becomes a test_runs row with its summary line.

    Given: PostToolUse for `npm test` whose stdout ends with a vitest summary.
    When: ingested.
    Then: test_runs holds passed=644, total=644 and the exact summary line.
    """
    output = "...\n Test Files  52 passed (52)\n      Tests  644 passed (644)\n   Duration  12.3s\n"
    write_spool(
        [
            spool_line(
                claude_payload(
                    "PostToolUse",
                    tool_name="Bash",
                    tool_use_id="toolu_t",
                    tool_input={"command": "npm test"},
                    tool_response={"stdout": output, "stderr": "", "interrupted": False},
                ),
                T0,
            )
        ]
    )
    ingest_spool(store, factory, paths.spool_dir, today=date(2026, 10, 23))
    [row] = store.query("SELECT runner, passed, total, summary_line FROM test_runs")
    assert (row["runner"], row["passed"], row["total"]) == ("vitest", 644, 644)
    assert row["summary_line"] == "Tests  644 passed (644)"


def test_cursor_shell_and_tool_hooks_count_one_test_run(
    store: Store, factory: EventFactory, paths: Paths, write_spool: Callable[..., Path]
) -> None:
    """TC-ING-011: One shell run reported by both afterShellExecution and postToolUse is one test run.

    Given: Cursor sends afterShellExecution and postToolUse for the same `go test ./...` run.
    When: ingested.
    Then: two events are stored but only one test_runs row.
    """
    out = "ok  \tgithub.com/ei/billing/internal/a\t0.3s\nok  \tgithub.com/ei/billing/internal/b\t(cached)\n"
    write_spool(
        [
            spool_line(
                cursor_payload(
                    "afterShellExecution", command="go test ./...", output=out, duration=900
                ),
                T0,
                "cursor",
            ),
            spool_line(
                cursor_payload(
                    "postToolUse",
                    tool_name="Shell",
                    tool_use_id="tu-go",
                    tool_input={"command": "go test ./..."},
                    tool_output=out,
                ),
                T0,
                "cursor",
            ),
        ]
    )
    ingest_spool(store, factory, paths.spool_dir, today=date(2026, 10, 23))
    assert _count(store) == 2
    assert _count(store, "SELECT COUNT(*) FROM test_runs") == 1


def test_excluded_paths_are_skipped_quietly(store: Store, paths: Paths, config: Config) -> None:
    """TC-ING-012: Hook events from a folder in capture.exclude_paths are dropped without error rows.

    Given: capture.exclude_paths = ["~/Desktop/archive/work_tracker"] and three hook events: one in
           that folder, one in a subfolder, one in a work repo.
    When: the spool is ingested.
    Then: only the work-repo event is stored, two are counted as skipped, and ingest_errors is empty.
    """
    cfg = Config.model_validate(
        {
            **config.model_dump(mode="json"),
            "capture": {"exclude_paths": ["~/Desktop/archive/work_tracker"]},
        }
    )
    tool = str(Path("~/Desktop/archive/work_tracker").expanduser())
    assert cfg.capture.exclude_paths == [Path(tool)]
    t0 = datetime(2026, 10, 26, 10, tzinfo=IST)
    lines = [
        spool_line(claude_payload("UserPromptSubmit", session="T1", cwd=tool, prompt="a"), t0),
        spool_line(
            claude_payload("UserPromptSubmit", session="T2", cwd=tool + "/app", prompt="b"), t0
        ),
        spool_line(
            claude_payload(
                "UserPromptSubmit", session="W1", cwd="/work/billing-service", prompt="c"
            ),
            t0,
        ),
    ]
    (paths.spool_dir / "2026-10-26.jsonl").write_text("\n".join(lines) + "\n")
    stats = ingest_spool(store, build_factory(cfg), paths.spool_dir, today=t0.date())
    assert stats.inserted == 1
    assert stats.skipped == 2
    assert store.scalar("SELECT session_key FROM events") == "claude:W1"
    assert store.scalar("SELECT COUNT(*) FROM ingest_errors") == 0
