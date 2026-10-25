"""Compliance check: standing rules and open asks against a draft (or any digest text)."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from workdigest.digest.document import parse_digest
from workdigest.digest.draft import Draft, Gap
from workdigest.digest.repository import Ask
from workdigest.digest.rules import RuleResult, StandingRule, evaluate

NUDGE_LEAD = timedelta(hours=1)


def check_text(text: str, rules: list[StandingRule]) -> list[RuleResult]:
    """Run every active rule on digest ``text``."""
    doc = parse_digest(text)
    return [evaluate(rule, doc) for rule in rules if rule.active]


def compliance_gaps(
    text: str,
    draft: Draft,
    *,
    rules: list[StandingRule],
    open_asks: list[Ask],
    now: datetime,
    send_deadline: time,
) -> list[Gap]:
    """Gaps from failed rules, the send deadline, and open asks that need a real action."""
    gaps: list[Gap] = []
    for result in check_text(text, rules):
        if result.passed is False:
            gaps.append(
                Gap(kind="rule", ref=result.rule_id, message=f"{result.text} → {result.message}")
            )
        elif result.passed is None and result.needs_real_action:
            gaps.append(
                Gap(
                    kind="real_action",
                    ref=result.rule_id,
                    message=f"{result.text} ({result.message})",
                )
            )
    deadline = datetime.combine(now.date(), send_deadline, tzinfo=now.tzinfo)
    if draft.work_date == now.date() and now >= deadline - NUDGE_LEAD:
        state = "passed" if now >= deadline else "is close"
        gaps.append(
            Gap(
                kind="rule",
                ref="R10-send-time",
                message=f"Send before {send_deadline:%H:%M}: the deadline {state}",
            )
        )

    # Pending asks are not part of the message; the ones that need a real action (not just
    # wording) stay on your gap list as a reminder until you answer them.
    for ask in open_asks:
        if ask.needs_real_action:
            times = f" (raised {ask.times_raised}x)" if ask.times_raised > 1 else ""
            gaps.append(
                Gap(
                    kind="real_action",
                    ref=ask.ask_id,
                    message=f"open ask needs a real action{times}: {ask.text[:110]}",
                )
            )
    return gaps
