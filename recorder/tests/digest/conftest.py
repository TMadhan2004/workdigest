"""Fixtures for the digest engine: the real 19–23 Oct digests and replies."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from workdigest.digest.document import ParsedDigest, parse_digest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "digests"
DATES = ("2026-10-19", "2026-10-21", "2026-10-22", "2026-10-23")


def digest_text(day: str) -> str:
    return (FIXTURES / day / "digest.txt").read_text(encoding="utf-8")


def reply_text(day: str) -> str:
    return (FIXTURES / day / "reply.txt").read_text(encoding="utf-8")


def history_text() -> str:
    """The fixtures re-assembled in the original 'Me:' / 'Reply:' format."""
    parts = []
    for day in DATES:
        parts.append("Me:\n" + digest_text(day))
        parts.append("Reply:\n" + reply_text(day))
    return "\n\n".join(parts)


@pytest.fixture(params=DATES)
def golden(request: pytest.FixtureRequest) -> tuple[str, ParsedDigest]:
    day = str(request.param)
    return day, parse_digest(digest_text(day))


def as_date(day: str) -> date:
    return date.fromisoformat(day)
