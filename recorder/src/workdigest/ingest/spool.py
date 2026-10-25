"""Incremental ingest of the hook spool (``spool/YYYY-MM-DD.jsonl``) into SQLite.

* Only complete lines are consumed. A line the hook is still writing (no trailing
  newline yet) is left for the next pass.
* The byte offset is stored per file, so re-running is idempotent. Events are also
  deduplicated by ``event_id``, which covers replays after a crash between insert and
  offset update.
* A bad line becomes an ``ingest_errors`` row and never stops the pass.
* Spool files older than the retention window are deleted once fully consumed, because
  they hold unredacted payloads.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from workdigest.clock import now_local
from workdigest.ingest.normalize import (
    EventFactory,
    IgnoredEventError,
    UnsupportedPayloadError,
    received_at_of,
)
from workdigest.ingest.sink import record
from workdigest.store.db import FileOffset, Store

SPOOL_RETENTION_DAYS = 2


@dataclass(slots=True)
class IngestStats:
    """Counters for one ingest pass."""

    lines: int = 0
    inserted: int = 0
    duplicates: int = 0
    errors: int = 0
    skipped: int = 0

    def merge(self, other: IngestStats) -> None:
        """Add ``other``'s counters to this one."""
        self.lines += other.lines
        self.inserted += other.inserted
        self.duplicates += other.duplicates
        self.errors += other.errors
        self.skipped += other.skipped


def ingest_spool(
    store: Store, factory: EventFactory, spool_dir: Path, today: date | None = None
) -> IngestStats:
    """Ingest every spool file in ``spool_dir``."""
    stats = IngestStats()
    if not spool_dir.is_dir():
        return stats
    for path in sorted(spool_dir.glob("*.jsonl")):
        stats.merge(_ingest_file(store, factory, path))
    _prune(store, spool_dir, today or now_local().date())
    return stats


def _ingest_file(store: Store, factory: EventFactory, path: Path) -> IngestStats:
    stats = IngestStats()
    key = str(path)
    stat = path.stat()
    previous = store.get_offset(key)
    start = previous.offset if previous and previous.offset <= stat.st_size else 0
    if start == stat.st_size:
        return stats
    with path.open("rb") as handle:
        handle.seek(start)
        chunk = handle.read(stat.st_size - start)
    end = chunk.rfind(b"\n")
    if end == -1:
        return stats  # only a partial line so far
    complete = chunk[: end + 1]
    with store.transaction():
        for line_no, raw_line in enumerate(complete.splitlines(), start=1):
            if not raw_line.strip():
                continue
            stats.lines += 1
            ref = f"{path.name}@{start}+{line_no}"
            try:
                spool_record = json.loads(raw_line)
            except ValueError as exc:
                stats.errors += 1
                store.log_error("spool.parse", str(exc), ref)
                continue
            payload = spool_record.get("payload")
            received_at = received_at_of(spool_record)
            if not isinstance(payload, dict) or received_at is None:
                stats.errors += 1
                store.log_error("spool.record", "record without JSON payload or received_at", ref)
                continue
            try:
                event = factory.from_hook(
                    payload, str(spool_record.get("source_hint", "")), received_at
                )
            except IgnoredEventError:
                stats.skipped += 1  # own LLM calls or excluded folders: expected, not an error
                continue
            except UnsupportedPayloadError as exc:
                stats.skipped += 1
                store.log_error("spool.normalize", str(exc), ref)
                continue
            if record(store, event):
                stats.inserted += 1
            else:
                stats.duplicates += 1
        store.set_offset(key, FileOffset(start + len(complete), stat.st_size, stat.st_mtime_ns, {}))
    return stats


def _prune(store: Store, spool_dir: Path, today: date) -> None:
    cutoff = today - timedelta(days=SPOOL_RETENTION_DAYS)
    for path in spool_dir.glob("*.jsonl"):
        try:
            file_day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        offset = store.get_offset(str(path))
        if file_day < cutoff and offset is not None and offset.offset >= path.stat().st_size:
            path.unlink(missing_ok=True)
