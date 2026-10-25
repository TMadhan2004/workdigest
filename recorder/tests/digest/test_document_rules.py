"""Parsing real digests, and golden compliance results checked against what Priya flagged."""

from __future__ import annotations

from datetime import date

import pytest

from tests.digest.conftest import DATES, digest_text, history_text
from workdigest.digest.document import (
    ParsedDigest,
    parse_digest,
    parse_digest_date,
    parse_history,
    section_key,
)
from workdigest.digest.rules import SEED_RULES, StandingRule, evaluate


def test_parses_all_golden_digests(golden: tuple[str, ParsedDigest]) -> None:
    """TC-DOC-001: Each real digest parses into its date, Jira list and core sections.

    Given: the 19, 21, 22 and 23 Oct digests as sent.
    When: parse_digest() runs.
    Then: the date matches the folder, every Jira entry has a key, title and URL, and
          done_today, schedule, blockers, risks and plan_upcoming are found.
    """
    day, doc = golden
    assert doc.work_date == date.fromisoformat(day)
    assert doc.jira
    assert all(entry.url and entry.url.endswith(entry.key) for entry in doc.jira)
    keys = {s.key for s in doc.sections}
    assert {"done_today", "schedule", "blockers", "risks", "plan_upcoming"} <= keys
    assert doc.field_value("in short")


def test_section_layout_by_date() -> None:
    """TC-DOC-002: Section keys follow each digest's actual structure (9 sections on 19 Oct, 11 later).

    Given: the four golden digests.
    When: parsed.
    Then: 19 Oct has 9 sections with separate ai_tools and quality; 21–23 Oct have the 11-point order.
    """
    first = [s.key for s in parse_digest(digest_text("2026-10-19")).sections]
    assert first == [
        "done_today",
        "schedule",
        "blockers",
        "risks",
        "decisions",
        "decisions_pending",
        "ai_tools",
        "quality",
        "plan_upcoming",
    ]
    eleven = [
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
    for day in DATES[1:]:
        assert [s.key for s in parse_digest(digest_text(day)).sections] == eleven, day


def test_header_fields_preamble_and_closing() -> None:
    """TC-DOC-003: Header fields, a reply-style preamble and a closing paragraph are captured.

    Given: 22 Oct (preamble before the date line, Confluence-style extra link) and 23 Oct (project/team lines, closing).
    When: parsed.
    Then: the preamble starts with the PR explanation, 23 Oct has project and team, and the closing mentions EIT-8403.
    """
    d22 = parse_digest(digest_text("2026-10-22"))
    assert d22.preamble.startswith("Thanks for the detailed review")
    assert d22.links
    d23 = parse_digest(digest_text("2026-10-23"))
    assert d23.field_value("project") == "Platform Consolidation"
    assert "Rahul" in (d23.field_value("team members") or "")
    assert "EIT-8403" in d23.closing


def test_inline_section_values_and_bare_none() -> None:
    """TC-DOC-004: "7. Releases and deployment – None." is parsed as an inline value and a bare None.

    Given: the 23 Oct digest.
    When: parsed.
    Then: the releases section has inline "None." and is_bare_none; blockers is also bare None.
    """
    doc = parse_digest(digest_text("2026-10-23"))
    releases = doc.section("releases")
    assert releases is not None
    assert releases.inline == "None."
    assert releases.is_bare_none
    blockers = doc.section("blockers")
    assert blockers is not None
    assert blockers.is_bare_none


def test_history_split() -> None:
    """TC-DOC-005: A 'Me:' / 'Reply:' transcript splits into digest–reply pairs in order.

    Given: the four fixtures joined in the original format.
    When: parse_history() runs.
    Then: there are 4 pairs, each with a reply, in date order.
    """
    pairs = parse_history(history_text())
    assert [str(p.digest.work_date) for p in pairs] == list(DATES)
    assert all(p.reply for p in pairs)


@pytest.mark.parametrize(
    ("title", "key"),
    [
        ("Done today", "done_today"),
        ("Plan vs actual (against the 19 Oct five-day plan, day 3 of 5)", "plan_vs_actual"),
        ("Releases and deployment", "releases"),
        ("PRs and review turnaround", "prs"),
        ("How I use AI tools and check quality", "ai_tools"),
        ("How I check quality", "quality"),
        ("Decisions pending", "decisions_pending"),
        ("Plan for the next 5 days", "plan_upcoming"),
        ("Something else", "other"),
    ],
)
def test_section_key_aliases(title: str, key: str) -> None:
    """TC-DOC-006: Section titles map to canonical keys, including older wordings.

    Given: current and historical section titles.
    When: section_key() runs.
    Then: each maps to its canonical key, and unknown titles map to "other".
    """
    assert section_key(title) == key


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Mon, 19 Oct 2026", date(2026, 10, 19)),
        ("23 October 2026", date(2026, 10, 23)),
        ("garbage", None),
    ],
)
def test_parse_digest_date(text: str, expected: date | None) -> None:
    """TC-DOC-007: Digest dates parse with or without a weekday.

    Given: "Mon, 19 Oct 2026", "23 October 2026" and garbage.
    When: parse_digest_date() runs.
    Then: the dates come back, or None.
    """
    assert parse_digest_date(text) == expected


# What Priya flagged in each reply, expressed as the seed rules that must fail on that digest.
FLAGGED_BY_PRIYA = {
    "2026-10-19": {
        "R01-structure",
        "R07-risk-owner-date",
        "R06-rollout",
    },  # plan vs actual, releases, learnings, PR turnaround; Risks=None after 92 renames; rollout in deployment-instructions.md
    "2026-10-21": {
        "R07-risk-owner-date",
        "R08-plan-dates",
        "R04-atomic-commit",
    },  # risk no owner/date; plan no dates; atomic-commit missing
    "2026-10-22": {
        "R02-header",
        "R05-repo-links",
    },  # open with project/people line; share repo links
    "2026-10-23": {"R02-header", "R03-named-tests"},  # day rating; keep naming the unit tests
}
# What Priya acknowledged as done, which must pass.
ACKNOWLEDGED_BY_PRIYA = {
    "2026-10-22": {"R04-atomic-commit"},
    "2026-10-23": {"R04-atomic-commit", "R05-repo-links", "R01-structure"},
}


@pytest.mark.parametrize("day", DATES)
def test_compliance_matches_manager_feedback(day: str) -> None:
    """TC-RUL-001: The compliance check flags what the manager flagged and passes what he acknowledged.

    Given: each real digest and the seed standing rules.
    When: every rule is evaluated.
    Then: all rules tied to Priya's complaints for that day fail, and those tied to his
          acknowledgements pass (golden oracle from the real replies).
    """
    doc = parse_digest(digest_text(day))
    results = {r.rule_id: evaluate(r, doc) for r in SEED_RULES}
    failed = {rid for rid, res in results.items() if res.passed is False}
    assert FLAGGED_BY_PRIYA[day] <= failed, (day, failed)
    passed = {rid for rid, res in results.items() if res.passed is True}
    assert ACKNOWLEDGED_BY_PRIYA.get(day, set()) <= passed, (day, passed)


def test_structure_rule_lists_missing_sections() -> None:
    """TC-RUL-002: The 11-point rule names exactly the sections missing on 19 Oct.

    Given: the 19 Oct digest.
    When: R01-structure is evaluated.
    Then: the message lists plan_vs_actual, releases, prs and learnings.
    """
    rule = next(r for r in SEED_RULES if r.rule_id == "R01-structure")
    result = evaluate(rule, parse_digest(digest_text("2026-10-19")))
    assert result.message == "missing sections: plan_vs_actual, releases, prs, learnings"


@pytest.mark.parametrize(
    ("risks", "done", "passed"),
    [
        ("   None", "   - Renamed 92 columns via migrations", False),
        ("   None.", "   - Updated the README", True),
        ("   1. Legacy account data not copied – owner: me, by 28 Oct", "   - copied data", True),
        (
            "   1. Legacy account data not copied. This needs to be handled",
            "   - copied data",
            False,
        ),
    ],
)
def test_risk_owner_date_rule(risks: str, done: str, passed: bool) -> None:
    """TC-RUL-003: Risks need an owner and a date, and a bare None is allowed only after non-risky work.

    Given: risk sections with and without owner/date, after risky and non-risky work.
    When: R07 is evaluated.
    Then: only an owned, dated risk or a None after a non-risky change passes.
    """
    rule = next(r for r in SEED_RULES if r.rule_id == "R07-risk-owner-date")
    doc = parse_digest(f"1. Done today\n{done}\n2. Risks\n{risks}\n")
    assert evaluate(rule, doc).passed is passed


def test_named_tests_rule() -> None:
    """TC-RUL-004: Named unit tests satisfy R03, while bare counts do not.

    Given: a testing section with counts only, then one naming TestOTPCannotBeReusedConcurrently.
    When: R03 is evaluated.
    Then: counts-only fails and the named test passes.
    """
    rule = next(r for r in SEED_RULES if r.rule_id == "R03-named-tests")
    counts = parse_digest("1. Done today\n   1. Testing\n      - billing 650/650 tests pass\n")
    named = parse_digest(
        "1. Done today\n   1. Testing\n      - Added TestOTPCannotBeReusedConcurrently; 650/650 tests pass\n"
    )
    assert evaluate(rule, counts).passed is False
    assert evaluate(rule, named).passed is True


def test_manual_rules_are_not_auto_passed() -> None:
    """TC-RUL-005: Rules that need Jira or a real action report passed=None, never True.

    Given: R11 (ticket hygiene) and R12 (one In Progress).
    When: evaluated on any digest.
    Then: passed is None and needs_real_action is True.
    """
    doc = parse_digest(digest_text("2026-10-23"))
    for rule_id in ("R11-ticket-hygiene", "R12-one-in-progress"):
        result = evaluate(next(r for r in SEED_RULES if r.rule_id == rule_id), doc)
        assert result.passed is None
        assert result.needs_real_action


def test_regex_rule_trigger_not_applicable() -> None:
    """TC-RUL-006: A triggered regex rule passes as "not applicable" when its trigger is absent.

    Given: R06 (rollout steps) on a digest with no migration work.
    When: evaluated.
    Then: it passes with message "not applicable".
    """
    rule = next(r for r in SEED_RULES if r.rule_id == "R06-rollout")
    result = evaluate(rule, parse_digest("1. Done today\n   - Updated docs\n"))
    assert (result.passed, result.message) == (True, "not applicable")


def test_custom_manual_rule() -> None:
    """TC-RUL-007: A standing rule from a reply (manual kind) is reported for review, not auto-checked.

    Given: a manual rule "Join the scrum call on WFH days".
    When: evaluated.
    Then: passed is None and needs_real_action is False.
    """
    rule = StandingRule(rule_id="U-1", text="Join the scrum call on WFH days", check_kind="manual")
    result = evaluate(rule, parse_digest("x"))
    assert result.passed is None
    assert not result.needs_real_action
