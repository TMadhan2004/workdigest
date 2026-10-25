"""In-process checks of the hook entry point (the subprocess tests cover real invocation)."""

from __future__ import annotations

import io
import json
import sys

import pytest

from workdigest.hooks import hook_entry
from workdigest.paths import Paths


class _Stdin:
    def __init__(self, data: bytes) -> None:
        self.buffer = io.BytesIO(data)


def test_main_records_payload_in_process(
    paths: Paths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """TC-HOK-001: main() writes the record and returns 0 without printing for Claude events.

    Given: a Claude Stop payload on stdin.
    When: hook_entry.main() is called directly.
    Then: it returns 0, stdout is empty, and the spool contains the payload.
    """
    monkeypatch.setattr(
        sys, "stdin", _Stdin(json.dumps({"session_id": "s", "hook_event_name": "Stop"}).encode())
    )
    assert hook_entry.main(["hook", "claude_code"]) == 0
    assert capsys.readouterr().out == ""
    [file] = list(paths.spool_dir.glob("*.jsonl"))
    assert json.loads(file.read_text())["payload"]["hook_event_name"] == "Stop"


def test_main_swallows_every_exception(monkeypatch: pytest.MonkeyPatch, paths: Paths) -> None:
    """TC-HOK-002: Any internal failure, including stdin errors, still returns 0.

    Given: a stdin whose read raises, and separately an append that raises.
    When: main() runs.
    Then: it returns 0 both times.
    """

    class Broken:
        @property
        def buffer(self) -> io.BytesIO:
            raise OSError("stdin closed")

    monkeypatch.setattr(sys, "stdin", Broken())
    assert hook_entry.main(["hook"]) == 0
    monkeypatch.setattr(sys, "stdin", _Stdin(b"{}"))

    def fail(*_: object) -> None:
        raise PermissionError("nope")

    monkeypatch.setattr(hook_entry, "_append", fail)
    assert hook_entry.main(["hook", "cursor"]) == 0


def test_build_record_flags(paths: Paths) -> None:
    """TC-HOK-003: _build_record keeps dict payloads, keeps raw text otherwise, and flags truncation.

    Given: a JSON object, a JSON scalar and an oversized byte string.
    When: _build_record runs.
    Then: payload is set only for the object, raw is set otherwise, and truncated reflects the size.
    """
    record, _ = hook_entry._build_record("x", b'{"a": 1}')
    assert record["payload"] == {"a": 1}
    assert not record["truncated"]
    record, _ = hook_entry._build_record("x", b"42")
    assert record["raw"] == "42"
    record, _ = hook_entry._build_record("x", b"y" * (hook_entry.MAX_STDIN_BYTES + 5))
    assert record["truncated"] is True
    assert len(record["raw"]) == hook_entry.MAX_STDIN_BYTES
