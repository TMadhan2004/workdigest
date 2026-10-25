"""Best-effort backfill from Cursor agent transcripts.

What I verified on this Mac (Cursor 3.24.12): transcripts live at
``~/.cursor/projects/<slug>/agent-transcripts/<id>/<id>.jsonl``, with sub-agents under
``.../<id>/subagents/*.jsonl``. Each line is ``{"role", "message": {"content": [...]}}``
with ``text`` and ``tool_use`` blocks (``name``, ``input``), plus
``{"type": "turn_ended"}`` markers. Known limits:

* Lines carry no timestamps. Prompts embed ``<timestamp>…</timestamp>``. Tool calls
  inherit the last prompt time and are flagged ``ts_approx``.
* Tool results are not stored, so test counts come only from the Cursor
  ``afterShellExecution`` hook, never from transcripts.
* The project slug replaces ``/`` and ``.`` with ``-``, so the workspace path is
  reconstructed by walking the filesystem. Numeric slugs (no folder open) yield no cwd.
* Tool calls have no ids. When hooks already recorded tool calls for a conversation,
  the transcript's tool calls are skipped to avoid double counting.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from workdigest.clock import local_tz, now_local, parse_cursor_timestamp, parse_iso
from workdigest.ingest.normalize import OWN_SANDBOX_MARKER, EventFactory
from workdigest.ingest.sink import record
from workdigest.ingest.spool import IngestStats
from workdigest.models import Kind, Origin, Source, stable_hash
from workdigest.store.db import FileOffset, Store

_USER_QUERY = re.compile(r"<user_query>\s*(?P<q>.*?)\s*</user_query>", re.S)
_TIMESTAMP_TAG = re.compile(r"<timestamp>.*?</timestamp>", re.S)
_SLUG_SEP = re.compile(r"[^A-Za-z0-9]")


def resolve_slug(slug: str, root: Path = Path("/")) -> Path | None:
    """Rebuild the folder path Cursor encoded as ``slug`` (``/`` and ``.`` become ``-``)."""
    target = slug.strip("-")
    if not target or target.isdigit():
        return None

    def walk(base: Path, remaining: str, depth: int) -> Path | None:
        if not remaining:
            return base
        if depth > 32:
            return None
        try:
            children = sorted(child for child in base.iterdir() if child.is_dir())
        except OSError:
            return None
        for child in children:
            encoded = _SLUG_SEP.sub("-", child.name)
            if remaining == encoded:
                return child
            if remaining.startswith(encoded + "-"):
                found = walk(child, remaining[len(encoded) + 1 :], depth + 1)
                if found is not None:
                    return found
        return None

    return walk(root, target, 0)


def collect_cursor_transcripts(
    store: Store,
    factory: EventFactory,
    projects_dir: Path,
    backfill_days: int,
    slug_root: Path = Path("/"),
) -> IngestStats:
    """Ingest new lines from recently modified Cursor agent transcripts."""
    stats = IngestStats()
    if not projects_dir.is_dir():
        return stats
    cutoff = (now_local() - timedelta(days=backfill_days + 1)).timestamp()
    slug_cache: dict[str, Path | None] = {}
    for path in sorted(projects_dir.glob("*/agent-transcripts/*/**/*.jsonl")):
        try:
            if path.stat().st_mtime < cutoff:
                continue
            slug = path.relative_to(projects_dir).parts[0]
            if OWN_SANDBOX_MARKER.replace("-", "") in slug.replace("-", ""):
                continue
            if slug not in slug_cache:
                slug_cache[slug] = resolve_slug(slug, slug_root)
            conversation_id = path.relative_to(projects_dir).parts[2]
            stats.merge(_ingest(store, factory, path, conversation_id, slug_cache[slug]))
        except OSError as exc:
            stats.errors += 1
            store.log_error("cursor_transcript.read", str(exc), str(path))
    return stats


@dataclass(slots=True)
class _Conversation:
    """Per-file context while reading one Cursor transcript chunk."""

    store: Store
    factory: EventFactory
    path: Path
    session_key: str
    cwd: str | None
    fallback_ts: datetime
    hooks_seen: bool
    state: dict[str, Any]
    stats: IngestStats = field(default_factory=IngestStats)

    def emit(self, draft_kind: Kind, draft: Any, ts: datetime, *, ts_approx: bool) -> None:
        inserted = record(
            self.store,
            self.factory.build(
                source=Source.CURSOR,
                origin=Origin.TRANSCRIPT,
                kind=draft_kind,
                natural_key=draft.natural_key,
                ts=ts,
                ts_approx=ts_approx,
                payload=draft.payload,
                session_key=self.session_key,
                cwd=self.cwd,
                ticket_texts=draft.ticket_texts,
            ),
        )
        self.stats.inserted += int(inserted)
        self.stats.duplicates += int(not inserted)

    def on_user_text(self, text: str) -> None:
        stamp = parse_cursor_timestamp(text)
        if stamp is not None:
            self.state["last_ts"] = stamp.isoformat()
        query = _USER_QUERY.search(text)
        prompt = query["q"] if query else _TIMESTAMP_TAG.sub("", text).strip()
        if not prompt:
            return
        draft = self.factory.prompt_draft(prompt)
        draft.payload["transcript_path"] = str(self.path)
        ts = stamp or _last_ts(self.state) or self.fallback_ts
        self.emit(Kind.PROMPT, draft, ts, ts_approx=stamp is None)

    def on_tool_use(self, block: dict[str, Any], line_offset: int) -> None:
        if self.hooks_seen:
            return
        name = str(block.get("name") or "")
        tool_input = block.get("input")
        draft = self.factory.tool_draft(
            stable_hash(str(self.path), line_offset, name, tool_input), name, tool_input, None
        )
        self.emit(Kind.TOOL_USE, draft, _last_ts(self.state) or self.fallback_ts, ts_approx=True)

    def on_line(self, raw: bytes, line_offset: int) -> None:
        self.stats.lines += 1
        try:
            entry = json.loads(raw)
        except ValueError as exc:
            self.stats.errors += 1
            self.store.log_error("cursor_transcript.parse", str(exc), f"{self.path}@{line_offset}")
            return
        if not isinstance(entry, dict):
            return
        message = entry.get("message")
        blocks = message.get("content") if isinstance(message, dict) else None
        role = entry.get("role")
        for block in blocks if isinstance(blocks, list) else []:
            if not isinstance(block, dict):
                continue
            if role == "user" and block.get("type") == "text":
                self.on_user_text(str(block.get("text") or ""))
            elif role == "assistant" and block.get("type") == "tool_use":
                self.on_tool_use(block, line_offset)


def _ingest(
    store: Store,
    factory: EventFactory,
    path: Path,
    conversation_id: str,
    workspace: Path | None,
) -> IngestStats:
    stat = path.stat()
    previous = store.get_offset(str(path))
    start = previous.offset if previous and previous.offset <= stat.st_size else 0
    if start == stat.st_size:
        return IngestStats()
    with path.open("rb") as handle:
        handle.seek(start)
        chunk = handle.read(stat.st_size - start)
    end = chunk.rfind(b"\n")
    if end == -1:
        return IngestStats()
    if factory.is_excluded(str(workspace) if workspace else None):
        return IngestStats()
    session_key = f"cursor:{conversation_id}"
    conversation = _Conversation(
        store=store,
        factory=factory,
        path=path,
        session_key=session_key,
        cwd=str(workspace) if workspace else None,
        fallback_ts=datetime.fromtimestamp(stat.st_mtime, tz=local_tz()),
        hooks_seen=bool(
            store.scalar(
                "SELECT 1 FROM events WHERE session_key = ? AND origin = 'hook' "
                "AND kind IN ('tool_use', 'shell', 'file_edit') LIMIT 1",
                (session_key,),
            )
        ),
        state=dict(previous.state) if previous and start else {},
    )
    with store.transaction():
        position = start
        for raw in chunk[: end + 1].splitlines(keepends=True):
            line_offset = position
            position += len(raw)
            if raw.strip():
                conversation.on_line(raw, line_offset)
        store.set_offset(
            str(path),
            FileOffset(start + end + 1, stat.st_size, stat.st_mtime_ns, conversation.state),
        )
    return conversation.stats


def _last_ts(state: dict[str, Any]) -> datetime | None:
    value = state.get("last_ts")
    return parse_iso(value) if isinstance(value, str) else None
