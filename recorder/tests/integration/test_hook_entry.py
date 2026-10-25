"""The hook script as agents run it: a real subprocess with stdin JSON."""

from __future__ import annotations

import json
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.conftest import claude_payload, cursor_payload, run_hook
from workdigest.paths import Paths

pytestmark = pytest.mark.integration


def _spool_records(paths: Paths) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for file in sorted(paths.spool_dir.glob("*.jsonl")):
        records.extend(json.loads(line) for line in file.read_text().splitlines() if line.strip())
    return records


def test_hook_appends_valid_record(paths: Paths) -> None:
    """TC-REC-001: The hook appends one well-formed spool record per invocation.

    Given: a Claude Code PostToolUse payload on stdin.
    When: the hook script runs.
    Then: it exits 0 with empty stdout, and the spool holds one record with the payload,
          source hint, timezone-aware receive time and schema version.
    """
    payload = claude_payload("PostToolUse", tool_name="Bash", tool_use_id="toolu_1")
    result = run_hook(payload)
    assert result.returncode == 0
    assert result.stdout == b""
    [record] = _spool_records(paths)
    assert record["payload"] == payload
    assert record["source_hint"] == "claude_code"
    assert record["v"] == 1
    assert "+" in str(record["received_at"]) or "-" in str(record["received_at"])[19:]


def test_hook_spool_file_is_private(paths: Paths) -> None:
    """TC-REC-002: Spool files are created owner-only (0600) because they hold raw payloads.

    Given: no spool file exists yet.
    When: the hook writes the first record.
    Then: the new file has mode 0600.
    """
    run_hook(claude_payload("Stop"))
    [file] = list(paths.spool_dir.glob("*.jsonl"))
    assert file.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("stdin", "expect_raw"),
    [(b"not json at all", True), (b"", True), (b"[1, 2, 3]", True), (b"\xff\xfe\x00garbage", True)],
)
def test_hook_tolerates_malformed_stdin(paths: Paths, stdin: bytes, expect_raw: bool) -> None:
    """TC-REC-003: Malformed or empty stdin never fails the hook.

    Given: stdin that is not a JSON object (text, empty, array, invalid UTF-8).
    When: the hook runs.
    Then: it exits 0, prints nothing, and keeps the raw text for diagnosis.
    """
    result = run_hook(stdin)
    assert result.returncode == 0
    assert result.stdout == b""
    assert result.stderr == b""
    [record] = _spool_records(paths)
    assert ("raw" in record) is expect_raw
    assert "payload" not in record


def test_hook_survives_unwritable_spool(tmp_path: Path) -> None:
    """TC-REC-004: An unwritable spool location never blocks or fails the agent.

    Given: WORKDIGEST_HOME points at a regular file, so the spool dir cannot be created.
    When: the hook runs.
    Then: it exits 0 quickly, with no stdout or stderr.
    """
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    env = {**os.environ, "WORKDIGEST_HOME": str(blocker)}
    started = time.perf_counter()
    result = run_hook(claude_payload("Stop"), env=env)
    elapsed = time.perf_counter() - started
    assert result.returncode == 0
    assert result.stdout == b""
    assert result.stderr == b""
    assert elapsed < 2.0


def test_hook_survives_read_only_spool(paths: Paths) -> None:
    """TC-REC-005: A read-only spool directory is tolerated silently.

    Given: the spool directory exists but is not writable.
    When: the hook runs.
    Then: it exits 0 with no output.
    """
    paths.spool_dir.chmod(0o500)
    try:
        result = run_hook(claude_payload("Stop"))
    finally:
        paths.spool_dir.chmod(0o700)
    assert result.returncode == 0
    assert result.stdout == b""
    assert result.stderr == b""


def test_hook_truncates_oversized_input(paths: Paths) -> None:
    """TC-REC-006: Payloads over 1 MB are truncated and flagged, not rejected.

    Given: a 1.5 MB stdin payload.
    When: the hook runs.
    Then: it exits 0 and the record has truncated=true and at most 1 MB of raw text.
    """
    big = json.dumps(claude_payload("UserPromptSubmit", prompt="x" * 1_500_000)).encode()
    result = run_hook(big)
    assert result.returncode == 0
    [record] = _spool_records(paths)
    assert record["truncated"] is True
    assert len(str(record["raw"]).encode()) <= 1_000_000


def test_hook_cursor_prompt_gets_continue_true(paths: Paths) -> None:
    """TC-REC-007: Cursor's beforeSubmitPrompt gets an explicit allow; Claude's prompt hook stays silent.

    Given: a Cursor beforeSubmitPrompt payload, then a Claude UserPromptSubmit payload.
    When: the hook runs for each.
    Then: Cursor receives {"continue": true} on stdout. Claude receives empty stdout,
          because Claude would inject stdout into the prompt context.
    """
    cursor_result = run_hook(cursor_payload("beforeSubmitPrompt", prompt="hi"), source="cursor")
    assert json.loads(cursor_result.stdout) == {"continue": True}
    claude_result = run_hook(claude_payload("UserPromptSubmit", prompt="hi"))
    assert claude_result.stdout == b""
    assert len(_spool_records(paths)) == 2


def test_hook_concurrent_writers_never_interleave(paths: Paths) -> None:
    """TC-REC-008: Parallel agents writing at once produce intact, complete lines.

    Given: 40 hook processes started concurrently from 4 sessions, each with a ~64 KB prompt.
    When: they all append to the same spool file.
    Then: there are exactly 40 records, each valid JSON, with 10 per session.
    """

    def fire(i: int) -> int:
        payload = claude_payload(
            "UserPromptSubmit", session=f"s{i % 4}", prompt=f"{i}-" + "y" * 65_000
        )
        return run_hook(payload).returncode

    with ThreadPoolExecutor(max_workers=16) as pool:
        codes = list(pool.map(fire, range(40)))
    assert codes == [0] * 40
    records = _spool_records(paths)
    assert len(records) == 40
    per_session: dict[str, int] = {}
    for record in records:
        session = record["payload"]["session_id"]  # type: ignore[index]
        per_session[session] = per_session.get(session, 0) + 1
    assert per_session == {"s0": 10, "s1": 10, "s2": 10, "s3": 10}


def test_hook_latency_budget(paths: Paths) -> None:
    """TC-REC-009: The hook is fast enough to never be felt in a live session.

    Given: 15 sequential invocations.
    When: each one's wall time is measured, including interpreter start-up.
    Then: the median is under 150 ms. That is a CI-safe bound; on an M-series Mac it
          is typically about 30 ms, and the hooks also run with async: true.
    """
    timings = []
    for _ in range(15):
        started = time.perf_counter()
        run_hook(claude_payload("PostToolUse", tool_name="Read"))
        timings.append(time.perf_counter() - started)
    assert statistics.median(timings) < 0.150
