"""Time helpers: parsing timestamps and mapping them to work days."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo


def local_tz() -> tzinfo:
    """Return the machine's current local timezone."""
    tz = datetime.now().astimezone().tzinfo
    assert tz is not None  # noqa: S101 - astimezone() always attaches a tzinfo
    return tz


def now_local() -> datetime:
    """Return the current time as an aware local datetime."""
    return datetime.now(tz=local_tz())


def parse_iso(value: str) -> datetime | None:
    """Parse an ISO-8601 timestamp (``Z`` allowed). Naive values are taken as UTC."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def work_date(ts: datetime, boundary_hour: int, tz: tzinfo | None = None) -> date:
    """Return the work day ``ts`` belongs to.

    Work before ``boundary_hour`` (local time) counts toward the previous day, so a
    late-night session after midnight lands on the day it started.
    """
    local = ts.astimezone(tz or local_tz())
    return (local - timedelta(hours=boundary_hour)).date()


def day_window(
    day: date, boundary_hour: int, tz: tzinfo | None = None
) -> tuple[datetime, datetime]:
    """Return the ``[start, end)`` local datetimes covered by work day ``day``."""
    zone = tz or local_tz()
    start = datetime.combine(day, time(hour=boundary_hour), tzinfo=zone)
    return start, start + timedelta(days=1)


_CURSOR_TS = re.compile(
    r"(?P<body>[A-Za-z]+, [A-Za-z]{3} \d{1,2}, \d{4}, \d{1,2}:\d{2} [AP]M) "
    r"\(UTC(?P<sign>[+-])(?P<hh>\d{1,2})(?::(?P<mm>\d{2}))?\)"
)


def parse_cursor_timestamp(text: str) -> datetime | None:
    """Parse Cursor's ``Wednesday, Sep 23, 2026, 9:36 AM (UTC+5:30)`` prompt stamp."""
    match = _CURSOR_TS.search(text)
    if not match:
        return None
    try:
        naive = datetime.strptime(match["body"], "%A, %b %d, %Y, %I:%M %p")  # noqa: DTZ007
    except ValueError:
        return None
    offset = timedelta(hours=int(match["hh"]), minutes=int(match["mm"] or 0))
    if match["sign"] == "-":
        offset = -offset
    return naive.replace(tzinfo=timezone(offset))
