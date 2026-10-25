"""Backfill from Claude Code transcripts (``~/.claude/projects/<slug>/<session>.jsonl``).

This collector is the fallback for hooks that were not installed, failed or were
skipped. It reads incrementally, by byte offset per file, and emits the same natural
keys as the hook path (``tool_use_id`` for tool calls, normalized text for prompts), so
facts already captured by a hook are not duplicated.

Tool calls are emitted when their ``tool_result`` line arrives, because the result holds
the test output. Calls whose result has not arrived yet are kept in
``pending_tool_uses`` between passes.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

from workdigest.clock import now_local, parse_iso
from workdigest.ingest.normalize import OWN_SANDBOX_MARKER, EventFactory
from workdigest.ingest.sink import record
from workdigest.ingest.spool import IngestStats
from workdigest.models import Kind, Origin, Source
from workdigest.store.db import FileOffset, Store

_SKIP_PROMPT_PREFIXES = ("<command-", "<local-command", "<system-reminder>", "Caveat:")
# IDE context blocks the extension adds next to the prompt; the hook's `prompt` omits them.
_CONTEXT_BLOCK_PREFIXES = ("<ide_", "<system-reminder>")


def collect_claude_transcripts(
    store: Store, factory: EventFactory, projects_dir: Path, backfill_days: int
) -> IngestStats:
    """Ingest new lines from every recently modified Claude Code transcript."""
    stats = IngestStats()
    if not projects_dir.is_dir():
        return stats
    cutoff = (now_local() - timedelta(days=backfill_days + 1)).timestamp()
    for path in sorted(projects_dir.glob("**/*.jsonl")):
        try:
            if path.stat().st_mtime < cutoff or OWN_SANDBOX_MARKER in str(path):
                continue
            stats.merge(_ingest_transcript(store, factory, path))
        except OSError as exc:
            stats.errors += 1
            store.log_error("claude_transcript.read", str(exc), str(path))
    return stats


def _iter_new_lines(store: Store, path: Path) -> tuple[list[bytes], FileOffset]:
    stat = path.stat()
    previous = store.get_offset(str(path))
    start = previous.offset if previous and previous.offset <= stat.st_size else 0
    if start == stat.st_size:
        return [], FileOffset(start, stat.st_size, stat.st_mtime_ns, {})
    with path.open("rb") as handle:
        handle.seek(start)
        chunk = handle.read(stat.st_size - start)
    end = chunk.rfind(b"\n")
    if end == -1:
        return [], FileOffset(start, stat.st_size, stat.st_mtime_ns, {})
    return chunk[: end + 1].splitlines(), FileOffset(
        start + end + 1, stat.st_size, stat.st_mtime_ns, {}
    )


def _ingest_transcript(store: Store, factory: EventFactory, path: Path) -> IngestStats:
    stats = IngestStats()
    lines, new_offset = _iter_new_lines(store, path)
    if not lines:
        return stats
    with store.transaction():
        for raw in lines:
            if not raw.strip():
                continue
            stats.lines += 1
            try:
                entry = json.loads(raw)
            except ValueError as exc:
                stats.errors += 1
                store.log_error("claude_transcript.parse", str(exc), str(path))
                continue
            if not isinstance(entry, dict):
                continue
            for inserted in _handle_entry(store, factory, entry, path):
                if inserted:
                    stats.inserted += 1
                else:
                    stats.duplicates += 1
        store.set_offset(str(path), new_offset)
    return stats


def _handle_entry(
    store: Store, factory: EventFactory, entry: dict[str, Any], path: Path
) -> Iterator[bool]:
    session_id = entry.get("sessionId")
    ts = parse_iso(str(entry.get("timestamp") or ""))
    entry_type = entry.get("type")
    if not session_id:
        return
    session_key = f"claude:{session_id}"
    if entry_type == "ai-title" and entry.get("aiTitle"):
        store.conn.execute(
            "UPDATE sessions SET title = ? WHERE session_key = ?",
            (str(entry["aiTitle"]), session_key),
        )
        return
    if ts is None or entry_type not in {"user", "assistant"}:
        return
    message = entry.get("message") or {}
    content = message.get("content")
    cwd = entry.get("cwd")
    if factory.is_excluded(cwd):
        return
    branch = entry.get("gitBranch")
    branch = None if branch in (None, "", "HEAD") else str(branch)
    common: dict[str, Any] = {
        "source": Source.CLAUDE_CODE,
        "origin": Origin.TRANSCRIPT,
        "ts": ts,
        "session_key": session_key,
        "cwd": cwd,
        "branch": branch,
    }

    if entry_type == "user":
        text = _prompt_text(content)
        if text is not None and not entry.get("isMeta") and not entry.get("isSidechain"):
            draft = factory.prompt_draft(text)
            draft.payload["transcript_path"] = str(path)
            yield record(
                store,
                factory.build(
                    kind=Kind.PROMPT,
                    natural_key=draft.natural_key,
                    payload=draft.payload,
                    ticket_texts=draft.ticket_texts,
                    **common,
                ),
            )
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                pending = store.pop_pending_tool_use(str(block.get("tool_use_id") or ""))
                if pending is None:
                    continue
                draft = factory.tool_draft(
                    pending["id"],
                    pending["name"],
                    pending["input"],
                    _result_text(block.get("content")),
                )
                started = parse_iso(pending.get("ts", "")) or ts
                yield record(
                    store,
                    factory.build(
                        kind=Kind.TOOL_USE,
                        natural_key=draft.natural_key,
                        payload=draft.payload,
                        ticket_texts=draft.ticket_texts,
                        **{**common, "ts": started},
                    ),
                )
        return

    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
            store.put_pending_tool_use(
                str(block["id"]),
                session_key,
                {
                    "id": block["id"],
                    "name": block.get("name", ""),
                    "input": block.get("input"),
                    "ts": ts.isoformat(),
                },
            )


def _prompt_text(content: Any) -> str | None:
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        texts = [
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict)
            and b.get("type") == "text"
            and not str(b.get("text", "")).lstrip().startswith(_CONTEXT_BLOCK_PREFIXES)
        ]
        if not texts:
            return None
        text = "\n".join(texts)
    else:
        return None
    stripped = text.strip()
    if not stripped or stripped.startswith(_SKIP_PROMPT_PREFIXES):
        return None
    return text


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return ""
