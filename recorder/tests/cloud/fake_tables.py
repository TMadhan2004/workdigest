"""In-memory PostgREST stand-in shared by the worker, daemon and end-to-end tests."""

from __future__ import annotations

import uuid
from typing import Any


class FakeTables:
    """Implements the subset of PostgREST filters the sync uses: eq., gt. and limit/order/select."""

    def __init__(self) -> None:
        self.rows: dict[str, list[dict[str, Any]]] = {}
        self.upserts: dict[str, int] = {}

    @property
    def user_id(self) -> str:
        return "u-1"

    @staticmethod
    def _match(row: dict[str, Any], filters: dict[str, str]) -> bool:
        for key, expr in filters.items():
            if key in {"select", "order", "limit"}:
                continue
            op, _, value = expr.partition(".")
            current = row.get(key)
            if op == "eq" and str(current).lower() != value.lower():
                return False
            if op == "gt" and not (current is not None and str(current) > value):
                return False
        return True

    def select(self, table: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        params = params or {}
        found = [dict(r) for r in self.rows.get(table, []) if self._match(r, params)]
        return found[: int(params["limit"])] if "limit" in params else found

    def upsert(self, table: str, rows: list[dict[str, Any]], on_conflict: str) -> None:
        keys = on_conflict.split(",")
        existing = self.rows.setdefault(table, [])
        for row in rows:
            match = next((r for r in existing if all(r.get(k) == row.get(k) for k in keys)), None)
            if match:
                match.update(row)
            else:
                existing.append(dict(row))
        self.upserts[table] = self.upserts.get(table, 0) + len(rows)

    def insert(self, table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        created = [
            {
                "id": str(uuid.uuid4()),
                "status": "queued",
                "updated_at": "2099-01-01T00:00:00+00:00",
                **r,
            }
            for r in rows
        ]
        self.rows.setdefault(table, []).extend(created)
        return created

    def update(
        self, table: str, filters: dict[str, str], values: dict[str, Any]
    ) -> list[dict[str, Any]]:
        changed = []
        for row in self.rows.get(table, []):
            if self._match(row, filters):
                row.update(values)
                changed.append(dict(row))
        return changed
