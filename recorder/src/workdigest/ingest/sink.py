"""Single write path for events, so every collector gets the same side effects."""

from __future__ import annotations

from workdigest.models import Event, TestRun
from workdigest.store.db import Store


def record(store: Store, event: Event) -> bool:
    """Insert ``event`` and any test result it carries. Return True if the event was new."""
    inserted = store.insert_event(event)
    result = event.payload.get("test_result")
    if inserted and isinstance(result, dict):
        store.insert_test_run(
            event, TestRun.model_validate(result), str(event.payload.get("command", ""))
        )
    return inserted
