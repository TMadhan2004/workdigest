#!/usr/bin/env python3
"""WorkDigest hook entry point for Claude Code and Cursor.

This file is copied verbatim to ``<WORKDIGEST_HOME>/bin/workdigest-hook.py`` and run
by the agent on every hook event. It must never slow down or break a live agent session,
so it follows strict rules:

* standard library only, compatible with Python 3.8+ (it may run under any ``python3``);
* no parsing beyond ``json.loads``, no redaction, no network, no SQLite;
* every exception is swallowed and the process always exits 0;
* stdout stays empty, except for Cursor's ``beforeSubmitPrompt`` hook, which expects an
  explicit ``{"continue": true}``. For Claude Code's ``UserPromptSubmit`` hook, stdout
  would be injected into the prompt context, so nothing is printed there.

The daemon reads the spool, redacts it and normalizes it into events.
"""

import datetime
import fcntl
import json
import os
import sys
from typing import Any, Dict, List, Tuple

MAX_STDIN_BYTES = 1_000_000
SPOOL_SCHEMA_VERSION = 1
_DEFAULT_HOME = "~/Library/Application Support/WorkDigest"
_CURSOR_PROMPT_EVENT = "beforeSubmitPrompt"


def _home() -> str:
    return os.environ.get("WORKDIGEST_HOME") or os.path.expanduser(_DEFAULT_HOME)


def _build_record(source_hint: str, raw: bytes) -> Tuple[Dict[str, Any], datetime.datetime]:
    truncated = len(raw) > MAX_STDIN_BYTES
    text = raw[:MAX_STDIN_BYTES].decode("utf-8", "replace")
    now = datetime.datetime.now().astimezone()
    record: Dict[str, Any] = {
        "v": SPOOL_SCHEMA_VERSION,
        "source_hint": source_hint,
        "received_at": now.isoformat(timespec="milliseconds"),
        "truncated": truncated,
    }
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        record["payload"] = payload
    else:
        record["raw"] = text
    return record, now


def _append(record: Dict[str, Any], now: datetime.datetime) -> None:
    spool_dir = os.path.join(_home(), "spool")
    os.makedirs(spool_dir, mode=0o700, exist_ok=True)
    path = os.path.join(spool_dir, now.strftime("%Y-%m-%d") + ".jsonl")
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    data = line.encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        written = 0
        while written < len(data):
            written += os.write(fd, data[written:])
    finally:
        os.close(fd)


def main(argv: List[str]) -> int:
    """Read one hook payload from stdin and append it to today's spool file."""
    try:
        source_hint = argv[1] if len(argv) > 1 else "unknown"
        raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
        record, now = _build_record(source_hint, raw)
        payload = record.get("payload") or {}
        if payload.get("hook_event_name") == _CURSOR_PROMPT_EVENT:
            sys.stdout.write('{"continue": true}\n')
            sys.stdout.flush()
        _append(record, now)
    except BaseException:
        # Never surface an error to the agent: recording is best effort.
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
