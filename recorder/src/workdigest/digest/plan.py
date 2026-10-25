"""Five-day plans: extract them from digests and find the active one for a day.

A plan comes from a digest's "Plan for the next 5 days" section. It stays active for
``horizon`` working days, counting the day it was written as day 1: the 19 Oct (Mon) plan
is day 3 of 5 on Wed 21 Oct, as in the real digests. Plans written while one is still
active (21 and 22 Oct) do not replace it; the next plan takes over once the active one
has run its course.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from workdigest.digest.document import ParsedDigest

_ITEM = re.compile(r"^\s{0,6}(?:\d{1,2}[.)]|-|•)\s+(?P<text>.+)$")
_SUB_ITEM_INDENT = 6


def working_day_index(start: date, day: date) -> int:
    """Day number of ``day`` in a plan started on ``start`` (start = day 1, weekends skipped)."""
    if day < start:
        return 0
    count = 0
    current = start
    while current <= day:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


def plan_items(doc: ParsedDigest) -> list[str]:
    """Top-level items of the digest's plan section."""
    section = doc.section("plan_upcoming")
    if section is None:
        return []
    items: list[str] = []
    for line in section.lines:
        indent = len(line) - len(line.lstrip(" "))
        match = _ITEM.match(line)
        if match and indent < _SUB_ITEM_INDENT:
            items.append(match["text"].strip())
    return items


@dataclass(frozen=True, slots=True)
class ActivePlan:
    """The plan a day is measured against."""

    plan_date: date
    day: int
    horizon: int
    items: list[str]


def active_plan(plans: list[tuple[date, int, list[str]]], day: date) -> ActivePlan | None:
    """Pick the plan in force on ``day`` (see module docstring).

    A plan is sticky for its horizon: plans written during it do not replace it. When it
    has run out by ``day``, the most recent plan written before ``day`` takes over.
    """
    earlier = sorted((p for p in plans if p[0] < day), key=lambda p: p[0])
    current: tuple[date, int, list[str]] | None = None
    for plan in earlier:
        if current is None or working_day_index(current[0], plan[0]) > current[1]:
            current = plan
    if current is None:
        return None
    if working_day_index(current[0], day) > current[1]:
        current = earlier[-1]
    index = working_day_index(current[0], day)
    if index > current[1]:
        return None
    return ActivePlan(plan_date=current[0], day=index, horizon=current[1], items=current[2])
