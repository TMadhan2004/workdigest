"""Standing rules: the manager's lasting requirements, checked against every draft.

Each rule has a ``check_kind`` that decides how it is evaluated:

* ``section_present`` – named sections must exist and not be empty;
* ``header_present`` – header fields (project, team, in short, rating) must be filled;
* ``regex`` – a pattern must (or must not) appear, optionally only when a trigger matches;
* ``risk_owner_date`` – risks need an owner and a date, and no bare "None" after a risky change;
* ``named_tests`` – test reporting names tests, not only counts;
* ``plan_dates`` – plan items carry dates;
* ``manual`` – cannot be checked from text (needs Jira, time of sending, a real action).

The seed list comes from the 16–23 Oct replies; replies add more over time.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from workdigest.digest.document import ParsedDigest

CHECK_KINDS = (
    "section_present",
    "header_present",
    "regex",
    "risk_owner_date",
    "named_tests",
    "plan_dates",
    "manual",
)


class StandingRule(BaseModel):
    """One lasting requirement."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    text: str
    check_kind: str
    check_params: dict[str, Any] = Field(default_factory=dict)
    source: str = "seed"
    active: bool = True


class RuleResult(BaseModel):
    """Outcome of one rule on one digest."""

    model_config = ConfigDict(frozen=True)

    rule_id: str
    text: str
    passed: bool | None  # None = cannot be checked automatically
    message: str = ""
    needs_real_action: bool = False


SEED_RULES: tuple[StandingRule, ...] = (
    StandingRule(
        rule_id="R01-structure",
        text="Follow the 11-point structure, including plan vs actual, releases, PR links and turnaround, and learnings.",
        check_kind="section_present",
        check_params={
            "sections": [
                "done_today",
                "plan_vs_actual",
                "schedule",
                "blockers",
                "risks",
                "decisions",
                "releases",
                "prs",
                "learnings",
                "ai_tools",
                "plan_upcoming",
            ]
        },
    ),
    StandingRule(
        rule_id="R02-header",
        text="Open with one line on the project and the people you work with; include the 'In short' line and a day rating (1/3/7/9).",
        check_kind="header_present",
        check_params={"fields": ["project", "team members", "in short", "day rating"]},
    ),
    StandingRule(
        rule_id="R03-named-tests",
        text="Name the unit tests added, not just counts.",
        check_kind="named_tests",
    ),
    StandingRule(
        rule_id="R04-atomic-commit",
        text="Include the atomic-commit check result.",
        check_kind="regex",
        # A result is required ("passed", "4 commits fixed"); a bare mention or a placeholder is not enough.
        check_params={
            "pattern": r"atomic[- ]commit check[^\n\[]*?(?:pass|fail|fix|split|clean|ok\b|\d)",
            "message": "no atomic-commit check result",
        },
    ),
    StandingRule(
        rule_id="R05-repo-links",
        text="Include repo and branch links.",
        check_kind="regex",
        check_params={
            "pattern": r"https://(?:bitbucket\.org|github\.com)/[^\s]+/(?:commits/branch|branch|src)/",
            "message": "no repo/branch links",
        },
    ),
    StandingRule(
        rule_id="R06-rollout",
        text="When migrations are involved, put rollout/rollback steps in deployment-instructions.md and say so.",
        check_kind="regex",
        check_params={
            "trigger": r"(?i)\bmigrat|\bDDL\b|\brenam|\bdrop(?:ped)?\b.*\btable",
            "pattern": r"(?i)rollback|roll back|deployment-instructions",
            "message": "migration work without rollout/rollback steps or deployment-instructions.md",
        },
    ),
    StandingRule(
        rule_id="R07-risk-owner-date",
        text="Risks need an owner and a date; never write a bare 'None' after a risky change.",
        check_kind="risk_owner_date",
        check_params={
            "risky": r"(?i)\bmigrat|\brenam|\bencrypt|\bDDL\b|\bdrop|\bcopied\b|\bdelet|\bremoved?\b.*\btables?\b"
        },
    ),
    StandingRule(
        rule_id="R08-plan-dates",
        text="The plan for upcoming days has dates (and owners where possible).",
        check_kind="plan_dates",
    ),
    StandingRule(
        rule_id="R09-plan-day",
        text="Plan vs actual names the plan date and the day (day N of 5).",
        check_kind="regex",
        check_params={
            "pattern": r"(?i)day \d of \d",
            "section": "plan_vs_actual",
            "message": "plan vs actual does not say 'day N of 5'",
        },
    ),
    StandingRule(
        rule_id="R10-send-time",
        text="Send before 9pm.",
        check_kind="manual",
        check_params={"deadline": "21:00"},
    ),
    StandingRule(
        rule_id="R11-ticket-hygiene",
        text="Every ticket worked on has a branch created from it and a meaningful Activity comment.",
        check_kind="manual",
        check_params={"needs_real_action": True, "phase": 3},
    ),
    StandingRule(
        rule_id="R12-one-in-progress",
        text="Only one ticket In Progress at a time; defects get a Jira Bug linked to the parent ticket, even if caught before release.",
        check_kind="manual",
        check_params={"needs_real_action": True, "phase": 3},
    ),
)

_NAMED_TEST = re.compile(
    r"\bTest[A-Z]\w+|\btest_[a-z]\w+|\bTC-[A-Z]+-\d+|\bit\(['\"]|\b(?:should|must) [a-z]+ .+? (?:when|if)\b|"
    r"[\"'‘“][^\"'’”]{8,}[\"'’”]\s*(?:test|spec)|named:",
)
_DATE_LIKE = re.compile(
    r"(?i)\b(?:\d{1,2}[ -](?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* \d{1,2}|\d{4}-\d{2}-\d{2}|"
    r"mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|tomorrow|today|by eod)\b"
)
_OWNER_HINT = re.compile(r"(?i)\bowner\b|\b(?:me|i)\b|@\w+|\(([A-Z][a-z]+)\)")


def _team_names(doc: ParsedDigest) -> re.Pattern[str] | None:
    """The first names on the digest's own "Team members" line: they count as owners too."""
    team = doc.field_value("team members", "team") or ""
    names = {part.split()[0] for part in re.split(r",|\band\b|&", team) if part.strip()}
    names = {n for n in names if re.fullmatch(r"[A-Za-z][A-Za-z.'-]+", n)}
    if not names:
        return None
    return re.compile(r"(?i)\b(?:" + "|".join(sorted(map(re.escape, names))) + r")\b")


_RISK_ITEM = re.compile(r"^\s*(?:\d+\.|-|•)\s+(?P<text>.+)$")


def evaluate(rule: StandingRule, doc: ParsedDigest) -> RuleResult:  # noqa: PLR0911, PLR0912
    """Check ``rule`` against a parsed digest."""
    params = rule.check_params
    base = {"rule_id": rule.rule_id, "text": rule.text}
    kind = rule.check_kind
    if kind == "section_present":
        missing = [k for k in params.get("sections", []) if not _has_section(doc, k)]
        if missing:
            return RuleResult(
                **base, passed=False, message="missing sections: " + ", ".join(missing)
            )
        return RuleResult(**base, passed=True)
    if kind == "header_present":
        missing = [f for f in params.get("fields", []) if not doc.field_value(f)]
        if missing:
            return RuleResult(
                **base, passed=False, message="missing header lines: " + ", ".join(missing)
            )
        return RuleResult(**base, passed=True)
    if kind == "regex":
        section_key = params.get("section")
        haystack = doc.text
        if section_key:
            section = doc.section(section_key)
            haystack = f"{section.title}\n{section.body}" if section else ""
        trigger = params.get("trigger")
        if trigger and not re.search(trigger, doc.text):
            return RuleResult(**base, passed=True, message="not applicable")
        if re.search(params["pattern"], haystack, re.I):
            return RuleResult(**base, passed=True)
        return RuleResult(**base, passed=False, message=params.get("message", "pattern not found"))
    if kind == "risk_owner_date":
        return _check_risks(rule, doc)
    if kind == "named_tests":
        scope = "\n".join(
            s.body for s in doc.sections if s.key in {"done_today", "ai_tools", "quality"}
        )
        if not re.search(r"(?i)\btest", scope):
            return RuleResult(**base, passed=False, message="no testing reported")
        if _NAMED_TEST.search(scope):
            return RuleResult(**base, passed=True)
        return RuleResult(
            **base, passed=False, message="tests reported as counts only; name the unit tests added"
        )
    if kind == "plan_dates":
        section = doc.section("plan_upcoming")
        if section is None:
            return RuleResult(**base, passed=False, message="no plan for upcoming days")
        items = [
            line
            for line in section.lines
            if _RISK_ITEM.match(line) and not line.startswith("      ")
        ]
        undated = [i for i in items if not _DATE_LIKE.search(i)]
        if items and len(undated) == len(items):
            return RuleResult(**base, passed=False, message="plan items have no dates")
        return RuleResult(**base, passed=True)
    needs_action = bool(params.get("needs_real_action"))
    note = "checked from Jira in Phase 3" if params.get("phase") == 3 else "check manually"
    return RuleResult(**base, passed=None, message=note, needs_real_action=needs_action)


def _has_section(doc: ParsedDigest, key: str) -> bool:
    section = doc.section(key)
    if section is None and key == "ai_tools":
        section = doc.section("quality")
    return section is not None and bool(section.body.strip())


def _check_risks(rule: StandingRule, doc: ParsedDigest) -> RuleResult:
    base = {"rule_id": rule.rule_id, "text": rule.text}
    section = doc.section("risks")
    if section is None:
        return RuleResult(**base, passed=False, message="no risks section")
    if "[missing" in section.body:
        return RuleResult(**base, passed=False, message="risks not written yet")
    done = doc.section("done_today")
    risky = bool(done and re.search(rule.check_params.get("risky", r"$^"), done.body))
    if section.is_bare_none:
        if risky:
            return RuleResult(
                **base,
                passed=False,
                message="bare 'None' after a risky change (migrations, renames, encryption or data moves)",
            )
        return RuleResult(**base, passed=True)
    items = [m["text"] for line in section.body.splitlines() if (m := _RISK_ITEM.match(line))] or [
        section.body
    ]
    problems = []
    team = _team_names(doc)
    for item in items:
        if not (_OWNER_HINT.search(item) or (team and team.search(item))):
            problems.append(f"no owner: {item[:60]}")
        if not _DATE_LIKE.search(item):
            problems.append(f"no date: {item[:60]}")
    if problems:
        return RuleResult(**base, passed=False, message="; ".join(problems))
    return RuleResult(**base, passed=True)
