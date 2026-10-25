"""Claude Code and Cursor transcript backfill."""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

from tests.conftest import IST, claude_payload, cursor_payload, spool_line
from workdigest.collectors.claude_transcripts import collect_claude_transcripts
from workdigest.collectors.cursor_transcripts import collect_cursor_transcripts, resolve_slug
from workdigest.config import Config
from workdigest.ingest.normalize import EventFactory
from workdigest.ingest.spool import ingest_spool
from workdigest.paths import Paths
from workdigest.pipeline import build_factory
from workdigest.store.db import Store

NOW = datetime.now(tz=IST)
TS = NOW.astimezone(IST).isoformat()


def _claude_line(kind: str, session: str = "S1", **extra: Any) -> str:
    base = {
        "type": kind,
        "sessionId": session,
        "timestamp": TS,
        "cwd": "/tmp",
        "gitBranch": "HEAD",
        "uuid": f"u-{len(extra)}",
        "isSidechain": False,
    }
    return json.dumps({**base, **extra})


def _write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _bash_pair(tool_id: str, command: str, result: str) -> list[str]:
    return [
        _claude_line(
            "assistant",
            message={
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": "Bash",
                        "input": {"command": command},
                    }
                ],
            },
        ),
        _claude_line(
            "user",
            message={
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": result}],
            },
        ),
    ]


def test_claude_transcript_backfill(store: Store, factory: EventFactory, tmp_path: Path) -> None:
    """TC-TRN-001: Claude transcripts yield prompts, tool calls with results, tests and titles.

    Given: a transcript with a real prompt, a meta prompt, an IDE-selection block, a Bash
           `npm test` call with its result and an ai-title line.
    When: backfilled.
    Then: there is one prompt (with the IDE block ignored), one tool_use with test_result
          644/644, a test_runs row and the session title.
    """
    transcript = tmp_path / "claude" / "proj" / "S1.jsonl"
    _write(
        transcript,
        [
            _claude_line(
                "user",
                message={
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "<ide_selection>some code</ide_selection>"},
                        {"type": "text", "text": "Run the tests for EIT-8403"},
                    ],
                },
            ),
            _claude_line(
                "user",
                isMeta=True,
                message={"role": "user", "content": "<command-name>/clear</command-name>"},
            ),
            *_bash_pair("toolu_A", "npm test", "      Tests  644 passed (644)\n"),
            json.dumps({"type": "ai-title", "aiTitle": "Billing move", "sessionId": "S1"}),
        ],
    )
    stats = collect_claude_transcripts(store, factory, tmp_path / "claude", backfill_days=2)
    assert stats.inserted == 2
    kinds = sorted(r["kind"] for r in store.query("SELECT kind FROM events"))
    assert kinds == ["prompt", "tool_use"]
    [prompt] = store.query("SELECT payload, ticket_keys FROM events WHERE kind = 'prompt'")
    assert json.loads(prompt["payload"])["excerpt"] == "Run the tests for EIT-8403"
    assert json.loads(prompt["ticket_keys"]) == ["EIT-8403"]
    assert store.scalar("SELECT passed || '/' || total FROM test_runs") == "644/644"
    assert (
        store.scalar("SELECT title FROM sessions WHERE session_key = 'claude:S1'") == "Billing move"
    )


def test_claude_transcript_is_incremental_and_pending_survives(
    store: Store, factory: EventFactory, tmp_path: Path
) -> None:
    """TC-TRN-002: Incremental reads work, and a tool call whose result arrives later is still captured.

    Given: a pass that sees only the tool_use line, then a later pass after the tool_result is appended.
    When: backfilled twice.
    Then: the first pass emits nothing, the second emits one tool_use, and a third pass adds nothing.
    """
    transcript = tmp_path / "claude" / "proj" / "S1.jsonl"
    use, result = _bash_pair("toolu_B", "go test ./...", "ok  \tgithub.com/x/a\t0.1s\n")
    _write(transcript, [use])
    first = collect_claude_transcripts(store, factory, tmp_path / "claude", 2)
    _write(transcript, [result])
    second = collect_claude_transcripts(store, factory, tmp_path / "claude", 2)
    third = collect_claude_transcripts(store, factory, tmp_path / "claude", 2)
    assert (first.inserted, second.inserted, third.lines) == (0, 1, 0)


def test_hook_and_transcript_capture_dedupe(
    store: Store, factory: EventFactory, paths: Paths, write_spool: Any, tmp_path: Path
) -> None:
    """TC-TRN-003: The same tool call and prompt from a hook and from the transcript are stored once.

    Given: hook records for a prompt and a Bash call (toolu_C), and a transcript with the same two.
    When: the spool is ingested, then the transcript is backfilled.
    Then: there are exactly 2 events, and the transcript copies count as duplicates.
    """
    write_spool(
        [
            spool_line(
                claude_payload("UserPromptSubmit", session="S1", prompt="Run the tests"), NOW
            ),
            spool_line(
                claude_payload(
                    "PostToolUse",
                    session="S1",
                    tool_name="Bash",
                    tool_use_id="toolu_C",
                    tool_input={"command": "pytest"},
                    tool_response={"stdout": "=== 3 passed in 0.1s ===", "stderr": ""},
                ),
                NOW,
            ),
        ],
        name=f"{NOW.date().isoformat()}.jsonl",
    )
    ingest_spool(store, factory, paths.spool_dir, today=NOW.date())
    transcript = tmp_path / "claude" / "proj" / "S1.jsonl"
    _write(
        transcript,
        [
            _claude_line("user", message={"role": "user", "content": "Run the tests"}),
            *_bash_pair("toolu_C", "pytest", "=== 3 passed in 0.1s ==="),
        ],
    )
    stats = collect_claude_transcripts(store, factory, tmp_path / "claude", 2)
    assert (stats.inserted, stats.duplicates) == (0, 2)
    assert store.scalar("SELECT COUNT(*) FROM events") == 2
    assert store.scalar("SELECT COUNT(*) FROM test_runs") == 1


def test_claude_transcript_skips_old_files_and_bad_lines(
    store: Store, factory: EventFactory, tmp_path: Path
) -> None:
    """TC-TRN-004: Old transcripts are skipped, and corrupt lines are logged without stopping.

    Given: one transcript last modified 10 days ago, and a current one with a corrupt line
           before a valid prompt.
    When: backfilled with backfill_days=2.
    Then: only the current file is read, the prompt is stored and one error is logged.
    """
    old = tmp_path / "claude" / "p" / "OLD.jsonl"
    _write(old, [_claude_line("user", session="OLD", message={"role": "user", "content": "old"})])
    ten_days = NOW.timestamp() - 10 * 86400
    os.utime(old, (ten_days, ten_days))
    current = tmp_path / "claude" / "p" / "NEW.jsonl"
    _write(
        current,
        [
            "{broken",
            _claude_line("user", session="NEW", message={"role": "user", "content": "new work"}),
        ],
    )
    stats = collect_claude_transcripts(store, factory, tmp_path / "claude", backfill_days=2)
    assert stats.inserted == 1
    assert stats.errors == 1
    assert store.scalar("SELECT COUNT(*) FROM sessions WHERE session_key = 'claude:OLD'") == 0


def test_resolve_slug(tmp_path: Path) -> None:
    """TC-TRN-005: Cursor project slugs map back to real folders, even with dots and hyphens in names.

    Given: folders <tmp>/Users/Arjun.T/Desktop/microservices-redesign and .../micro.
    When: resolve_slug("Users-Arjun-T-Desktop-microservices-redesign") runs, rooted at tmp.
    Then: the hyphenated folder is found, and numeric or unknown slugs give None.
    """
    target = tmp_path / "Users" / "Arjun.T" / "Desktop" / "microservices-redesign"
    target.mkdir(parents=True)
    (tmp_path / "Users" / "Arjun.T" / "Desktop" / "micro").mkdir()
    assert resolve_slug("Users-Arjun-T-Desktop-microservices-redesign", tmp_path) == target
    assert resolve_slug("1776402787993", tmp_path) is None
    assert resolve_slug("Users-Nobody-Here", tmp_path) is None


def _cursor_transcript(
    root: Path, slug: str, conversation: str, lines: list[dict[str, Any]]
) -> Path:
    path = root / slug / "agent-transcripts" / conversation / f"{conversation}.jsonl"
    _write(path, [json.dumps(line) for line in lines])
    return path


def test_cursor_transcript_backfill(store: Store, factory: EventFactory, tmp_path: Path) -> None:
    """TC-TRN-006: Cursor transcripts yield timestamped prompts and approximate-time tool calls.

    Given: a Cursor transcript with a <timestamp>-tagged user query, two tool_use blocks
           and a turn_ended marker, in a slug that resolves to a workspace folder.
    When: backfilled.
    Then: the prompt has the tag's exact time, the tool calls are ts_approx, and the cwd is the workspace.
    """
    workspace = tmp_path / "fs" / "work" / "portal"
    workspace.mkdir(parents=True)
    stamp = NOW.strftime("%A, %b %d, %Y, %I:%M %p").replace(" 0", " ")
    lines = [
        {
            "role": "user",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": f"<timestamp>{stamp} (UTC+5:30)</timestamp>\n<user_query>\nFix EIT-9286 KDM save\n</user_query>",
                    }
                ]
            },
        },
        {
            "role": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Looking"},
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"path": str(workspace / "a.ts")},
                    },
                ]
            },
        },
        {
            "role": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Shell", "input": {"command": "npm test"}}]
            },
        },
        {"type": "turn_ended", "status": "success"},
    ]
    _cursor_transcript(tmp_path / "cursor", "work-portal", "conv-X", lines)
    stats = collect_cursor_transcripts(
        store, factory, tmp_path / "cursor", 2, slug_root=tmp_path / "fs"
    )
    assert stats.inserted == 3
    rows = store.query("SELECT kind, ts_approx, cwd, ticket_keys FROM events ORDER BY kind")
    assert [(r["kind"], r["ts_approx"]) for r in rows] == [
        ("prompt", 0),
        ("tool_use", 1),
        ("tool_use", 1),
    ]
    assert {r["cwd"] for r in rows} == {str(workspace)}
    assert json.loads(rows[0]["ticket_keys"]) == ["EIT-9286"]


def test_cursor_transcript_tools_skipped_when_hooks_present(
    store: Store, factory: EventFactory, paths: Paths, write_spool: Any, tmp_path: Path
) -> None:
    """TC-TRN-007: Cursor transcript tool calls are skipped when hooks already captured that conversation.

    Given: a hook-captured afterShellExecution for conv-H, and a transcript for conv-H with a tool_use.
    When: the spool is ingested, then the transcript is backfilled.
    Then: no extra tool_use is stored from the transcript (the prompt is still captured).
    """
    write_spool(
        [
            spool_line(
                cursor_payload(
                    "afterShellExecution", conversation="conv-H", command="ls", output="x"
                ),
                NOW,
                "cursor",
            )
        ],
        name=f"{NOW.date().isoformat()}.jsonl",
    )
    ingest_spool(store, factory, paths.spool_dir, today=date.today())  # noqa: DTZ011
    _cursor_transcript(
        tmp_path / "cursor",
        "123",
        "conv-H",
        [
            {
                "role": "user",
                "message": {
                    "content": [{"type": "text", "text": "<user_query>do it</user_query>"}]
                },
            },
            {
                "role": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "name": "Shell", "input": {"command": "ls"}}]
                },
            },
        ],
    )
    collect_cursor_transcripts(store, factory, tmp_path / "cursor", 2, slug_root=tmp_path)
    kinds = sorted(
        r["kind"]
        for r in store.query("SELECT kind FROM events WHERE session_key = 'cursor:conv-H'")
    )
    assert kinds == ["prompt", "shell"]


def test_cursor_subagent_transcripts_join_parent(
    store: Store, factory: EventFactory, tmp_path: Path
) -> None:
    """TC-TRN-008: Cursor sub-agent transcripts are attributed to the parent conversation.

    Given: a sub-agent transcript at conv-P/subagents/sa-1.jsonl.
    When: backfilled.
    Then: its events use session key cursor:conv-P.
    """
    path = tmp_path / "cursor" / "123" / "agent-transcripts" / "conv-P" / "subagents" / "sa-1.jsonl"
    _write(
        path,
        [
            json.dumps(
                {
                    "role": "user",
                    "message": {
                        "content": [{"type": "text", "text": "<user_query>sub task</user_query>"}]
                    },
                }
            )
        ],
    )
    collect_cursor_transcripts(store, factory, tmp_path / "cursor", 2, slug_root=tmp_path)
    assert store.scalar("SELECT session_key FROM events") == "cursor:conv-P"


def test_transcripts_skip_excluded_paths(store: Store, config: Config, tmp_path: Path) -> None:
    """TC-TRN-009: Claude and Cursor transcripts from a folder in capture.exclude_paths are not backfilled.

    Given: capture.exclude_paths covering a "work_tracker" folder, a Claude transcript whose cwd is
           inside it, and a Cursor transcript whose workspace is that folder.
    When: both collectors run.
    Then: nothing is inserted.
    """
    excluded = tmp_path / "fs" / "work_tracker"
    excluded.mkdir(parents=True)
    cfg = Config.model_validate(
        {**config.model_dump(mode="json"), "capture": {"exclude_paths": [str(excluded)]}}
    )
    factory = build_factory(cfg)
    claude_root = tmp_path / "claude"
    _write(
        claude_root / "proj" / "S9.jsonl",
        [
            _claude_line(
                "user",
                session="S9",
                cwd=str(excluded / "app"),
                message={"role": "user", "content": "fix it"},
            )
        ],
    )
    claude = collect_claude_transcripts(store, factory, claude_root, 2)
    _cursor_transcript(
        tmp_path / "cursor",
        "work-tracker",  # Cursor encodes "_", "." and "/" as "-"
        "conv-W",
        [
            {
                "role": "user",
                "message": {
                    "content": [{"type": "text", "text": "<user_query>\nhi\n</user_query>"}]
                },
            }
        ],
    )
    cursor = collect_cursor_transcripts(
        store, factory, tmp_path / "cursor", 2, slug_root=tmp_path / "fs"
    )
    assert claude.inserted == 0
    assert cursor.inserted == 0
    assert store.scalar("SELECT COUNT(*) FROM events") == 0
