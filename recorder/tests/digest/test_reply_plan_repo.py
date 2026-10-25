"""Reply parsing (golden), five-day plans, and the digest repository (asks, rules, history)."""

from __future__ import annotations

from datetime import date

import pytest

from tests.digest.conftest import as_date, digest_text, history_text, reply_text
from workdigest.config import Config
from workdigest.digest.document import parse_digest
from workdigest.digest.plan import active_plan, plan_items, working_day_index
from workdigest.digest.reply_parser import Segment, classify, due_date_of, parse_reply, segment
from workdigest.digest.repository import DigestRepository, is_same_ask, similarity
from workdigest.digest.service import add_reply, import_history
from workdigest.ingest.context import TicketMatcher
from workdigest.llm.fake import FakeProvider
from workdigest.store.db import Store

TICKETS = TicketMatcher(["EIT", "CA"])


def _find(parsed, needle: str):  # type: ignore[no-untyped-def]
    return next(i for i in parsed.items if needle in i.text)


# ------------------------------------------------------------------- replies
def test_headed_reply_segments_and_types() -> None:
    """TC-RPL-001: A reply with Bugs/Tasks/PRs/Factory/Digest/Attendance headings parses into typed items.

    Given: the real 21 Oct reply.
    When: parse_reply() runs (heuristics only).
    Then: the greeting is found; bullets carry their heading; the OTP bug and the EIT-8403 comment are
          one-time actions needing real action; "Before every PR, run the atomic-commit check" is a
          standing rule; attendance is info_only; the EIT-8383 status ask is linked to EIT-8383.
    """
    parsed = parse_reply(reply_text("2026-10-21"), TICKETS, as_date("2026-10-21"))
    assert parsed.greeting == "Hi Arjun,"
    headings = {i.heading for i in parsed.items}
    assert {"Bugs", "Tasks", "PRs", "Factory", "Digest", "Attendance"} <= headings
    otp = _find(parsed, "OTP fix is a security fix")
    assert (otp.type, otp.heading, otp.needs_real_action) == ("one_time_action", "Bugs", True)
    comment = _find(parsed, "10,500-character")
    assert comment.related_tickets == ["EIT-8403"]
    assert comment.needs_real_action
    assert _find(parsed, "Before every PR").type == "standing_rule"
    assert _find(parsed, "punched in at 9:03am").type == "info_only"
    assert _find(parsed, "EIT-8383 (https").related_tickets == ["EIT-8383"]
    assert _find(parsed, "For tomorrow, raise the OTP bug").type == "one_time_action"


def test_prose_reply_asks_questions_and_rules() -> None:
    """TC-RPL-002: A prose reply yields actions, questions, standing rules and a sign-off.

    Given: the real 22 Oct reply (no headings).
    When: parsed.
    Then: the sign-off is "Best Regards Priya Perumal"; "log each as a Jira Bug linked to EIT-8403" is an
          action on EIT-8403; the 10-agent crew line is a question; "From the next digest, open with one
          line…" and "keep only one In Progress" are standing rules; the due date 23-Oct-2026 is extracted.
    """
    parsed = parse_reply(reply_text("2026-10-22"), TICKETS, as_date("2026-10-22"))
    assert parsed.signoff == "Best Regards Priya Perumal"
    bug = _find(parsed, "log each as a Jira Bug")
    assert (bug.type, bug.related_tickets) == ("one_time_action", ["EIT-8403"])
    assert _find(parsed, "crew of at least 10 agents").type == "question"
    assert _find(parsed, "From the next digest").type == "standing_rule"
    assert _find(parsed, "keep only one In Progress").type == "standing_rule"
    assert _find(parsed, "Both are due").due_date == "2026-10-23"


def test_numbered_reply_and_ticket_extraction() -> None:
    """TC-RPL-003: Numbered asks become actions and Jira keys are extracted.

    Given: the real 19 Oct reply.
    When: parsed.
    Then: "Add plan vs actual…" and "Add the PR links…" are actions; the rollout question is a
          question; the CA-0214/CA-0215 recheck is linked to both keys.
    """
    parsed = parse_reply(reply_text("2026-10-19"), TICKETS, as_date("2026-10-19"))
    assert _find(parsed, "Add plan vs actual").type == "one_time_action"
    assert _find(parsed, "Add the PR links").type == "one_time_action"
    assert _find(parsed, "rollout and rollback plan").type == "question"
    assert _find(parsed, "recheck CA-0214").related_tickets == ["CA-0214", "CA-0215"]


def test_every_golden_reply_item_has_a_valid_type() -> None:
    """TC-RPL-004: Every segment of every real reply gets one of the five types, and nothing is dropped.

    Given: all four replies.
    When: parsed.
    Then: every item type is valid, indices run 0..n-1, and no sign-off text leaks into items.
    """
    for day in ("2026-10-19", "2026-10-21", "2026-10-22", "2026-10-23"):
        parsed = parse_reply(reply_text(day), TICKETS, as_date(day))
        assert [i.idx for i in parsed.items] == list(range(len(parsed.items)))
        assert all(
            i.type in {"standing_rule", "one_time_action", "question", "feedback", "info_only"}
            for i in parsed.items
        )
        assert not any("Best Regards" in i.text for i in parsed.items)


@pytest.mark.parametrize(
    ("text", "heading", "expected"),
    [
        ("Very good work on the migrator.", None, "feedback"),
        ("Will you keep naming the unit tests you add?", None, "question"),
        ("Please raise a bug for the OTP fix.", None, "one_time_action"),
        ("From the next digest, add a rating.", None, "standing_rule"),
        ("You were in at 8:53am today.", None, "info_only"),
        ("The plan has no dates.", "Digest", "standing_rule"),
        ("I'd still like the agent-crew demo.", None, "one_time_action"),
        ("Martin Fowler's Contract Tests fill this gap.", None, "info_only"),
    ],
)
def test_classify_heuristics(text: str, heading: str | None, expected: str) -> None:
    """TC-RPL-005: The heuristic classifier covers each type.

    Given: representative sentences (with optional heading).
    When: classify() runs.
    Then: the expected type is returned.
    """
    assert classify(Segment(0, text, heading)) == expected


def test_segment_wrapped_bullets_and_ellipsis() -> None:
    """TC-RPL-006: Wrapped bullet lines join their bullet, and "..." does not split sentences.

    Given: a bullet continued on an indented line, and "Very good... this reads well."
    When: segment() runs.
    Then: one bullet with the full text, and one sentence containing "...".
    """
    _, _, segs = segment(
        "Hi M,\n\nTasks\n- Add a comment on\n  EIT-1 today\n\nVery good... this reads well.\n"
    )
    assert segs[0].text == "Add a comment on EIT-1 today"
    assert segs[1].text == "Very good... this reads well."


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Fix it by tomorrow", "2026-10-23"),
        ("Both are due 23-Oct-2026", "2026-10-23"),
        ("due today", "2026-10-22"),
        ("no date", None),
    ],
)
def test_due_dates(text: str, expected: str | None) -> None:
    """TC-RPL-007: Due dates resolve explicit dates and "tomorrow"/"today" against the reply date.

    Given: reply date 22 Oct 2026.
    When: due_date_of() runs.
    Then: ISO dates come back, or None.
    """
    assert due_date_of(text, date(2026, 10, 22)) == expected


def test_llm_refines_types_but_cannot_add_items() -> None:
    """TC-RPL-008: An LLM may relabel existing items, but extra or unknown items are ignored.

    Given: a fake LLM relabelling item 0 as question and inventing idx 99.
    When: parse_reply(..., llm=fake) runs.
    Then: item 0 is a question with method "llm", there is no item 99, and the count is unchanged.
    """
    text = "Hi M,\n\nPlease share the branch list.\nGood work.\n"
    fake = FakeProvider(
        ['{"items": [{"idx": 0, "type": "question"}, {"idx": 99, "type": "feedback"}]}']
    )
    parsed = parse_reply(text, TICKETS, None, fake)
    assert len(parsed.items) == 2
    assert (parsed.items[0].type, parsed.items[0].method) == ("question", "llm")
    assert parsed.items[1].method == "heuristic"


def test_llm_garbage_falls_back_to_heuristics() -> None:
    """TC-RPL-009: Invalid LLM output never breaks parsing; the heuristic labels stand.

    Given: a fake LLM that returns prose twice (initial attempt and retry).
    When: parse_reply runs.
    Then: the items keep their heuristic types.
    """
    fake = FakeProvider(["sure! here you go", "still not json"])
    parsed = parse_reply("Please raise the OTP bug.", TICKETS, None, fake)
    assert parsed.items[0].type == "one_time_action"
    assert parsed.items[0].method == "heuristic"


# --------------------------------------------------------------------- plans
def test_working_day_index() -> None:
    """TC-PLN-001: Working days count the plan day as day 1 and skip weekends.

    Given: a plan on Mon 19 Oct.
    When: indexing Wed 21, Fri 23 and Mon 26 Oct.
    Then: 3, 5 and 6 respectively; a day before the plan is 0.
    """
    start = date(2026, 10, 19)
    assert [working_day_index(start, date(2026, 10, d)) for d in (21, 23, 26)] == [3, 5, 6]
    assert working_day_index(start, date(2026, 10, 18)) == 0


def test_active_plan_matches_real_digests() -> None:
    """TC-PLN-002: The active plan and day number match what the real digests said.

    Given: plans from all four golden digests.
    When: active_plan() runs for 21, 22 and 26 Oct.
    Then: 21 Oct is day 3 of the 19 Oct plan and 22 Oct is day 4 (as written in the digests);
          on 26 Oct the 23 Oct plan has taken over (day 2).
    """
    plans = [
        (as_date(d), 5, plan_items(parse_digest(digest_text(d))))
        for d in ("2026-10-19", "2026-10-21", "2026-10-22", "2026-10-23")
    ]
    p21 = active_plan(plans, date(2026, 10, 21))
    p22 = active_plan(plans, date(2026, 10, 22))
    p26 = active_plan(plans, date(2026, 10, 26))
    assert p21 is not None
    assert p22 is not None
    assert p26 is not None
    assert (p21.plan_date, p21.day, p22.day) == (date(2026, 10, 19), 3, 4)
    assert (p26.plan_date, p26.day) == (date(2026, 10, 23), 2)
    assert active_plan(plans, date(2026, 10, 19)) is None
    assert active_plan([], date(2026, 10, 21)) is None


def test_plan_items_top_level_only() -> None:
    """TC-PLN-003: Plan items are the top-level entries; sub-bullets are not separate items.

    Given: the 22 Oct plan (3 items, some with sub-bullets).
    When: plan_items() runs.
    Then: exactly 3 items are returned.
    """
    assert len(plan_items(parse_digest(digest_text("2026-10-22")))) == 3


# ---------------------------------------------------------------- repository
def test_history_import_seeds_everything(store: Store, config: Config) -> None:
    """TC-REP-001: Importing the 19–23 Oct history stores digests, plans, asks and new rules.

    Given: the four digest/reply pairs.
    When: import_history() runs twice.
    Then: 4 digests and 4 plans are stored; asks and rules are created once (the second import adds nothing);
          the repeated OTP-bug ask is merged with times_raised > 1.
    """
    first = import_history(store, config, history_text())
    second_asks = len(DigestRepository(store).asks())
    again = import_history(store, config, history_text())
    repo = DigestRepository(store)
    assert (first.digests, first.replies) == (4, 4)
    assert store.scalar("SELECT COUNT(*) FROM digests") == 4
    assert len(repo.plans()) == 4
    assert first.asks_new > 5
    assert len(repo.asks()) == second_asks
    assert again.asks_new == 0
    otp = [a for a in repo.asks() if "otp" in a.text.lower()]
    assert otp
    assert max(a.times_raised for a in otp) >= 2


def test_ask_status_rules(store: Store, config: Config) -> None:
    """TC-REP-002: Ask status changes enforce evidence for done and a reason for disagree.

    Given: an imported open ask.
    When: it is set to done without evidence, disagree without a reason, an invalid status, then done with evidence.
    Then: the first three raise ValueError, and the last closes it with closed_on set.
    """
    import_history(store, config, history_text())
    repo = DigestRepository(store)
    ask = repo.asks(status="open")[0]
    for status, kwargs in (("done", {}), ("disagree", {}), ("maybe", {})):
        with pytest.raises(ValueError, match=r"evidence|reason|status"):
            repo.set_ask_status(ask.ask_id, status, **kwargs)
    repo.set_ask_status(
        ask.ask_id[:6], "done", evidence="https://ei-study.atlassian.net/browse/EIT-9999"
    )
    closed = next(a for a in repo.asks() if a.ask_id == ask.ask_id)
    assert (closed.status, closed.evidence) == (
        "done",
        "https://ei-study.atlassian.net/browse/EIT-9999",
    )
    assert closed.closed_on


def test_rules_seed_idempotent_and_user_rules(store: Store, config: Config) -> None:
    """TC-REP-003: Seed rules insert once; similar reply rules are not duplicated.

    Given: seed_rules() called twice, then the same standing-rule reply confirmed twice.
    When: rules are listed.
    Then: 12 seed rules exist once, and the new reply rule is added once.
    """
    repo = DigestRepository(store)
    assert repo.seed_rules() == 12
    assert repo.seed_rules() == 0
    text = "From the next digest, mention the Confluence page for every ticket."
    add_reply(store, config, text, date(2026, 10, 24), None, confirm=True)
    add_reply(store, config, text + " ", date(2026, 10, 24), None, confirm=True)
    user_rules = [r for r in repo.rules() if r.source != "seed"]
    assert len(user_rules) == 1
    assert user_rules[0].check_kind == "manual"


def test_dismissed_items_are_skipped(store: Store, config: Config) -> None:
    """TC-REP-004: Items the user dismisses during review never become asks.

    Given: a two-item reply with item 0 dismissed before confirming.
    When: confirm_reply() runs.
    Then: only one ask is created.
    """
    repo = DigestRepository(store)
    reply_id, _, _ = add_reply(
        store, config, "Please raise the bug. Please share the list.", None, None, confirm=False
    )
    repo.set_item(f"{reply_id}:0", status="dismissed")
    counts = repo.confirm_reply(reply_id, date(2026, 10, 24))
    assert (counts["asks_new"], counts["skipped"]) == (1, 1)


def test_known_titles_and_latest_digest(store: Store, config: Config) -> None:
    """TC-REP-005: Ticket titles come from previous digests, and the previous digest is found by date.

    Given: imported history.
    When: known_ticket_titles() and latest_digest_before(24 Oct) run.
    Then: EIT-9286 has its title, and the previous digest is 23 Oct.
    """
    import_history(store, config, history_text())
    repo = DigestRepository(store)
    assert repo.known_ticket_titles()["EIT-9286"].startswith("Move SIS onboarding milestones")
    previous = repo.latest_digest_before(date(2026, 10, 24))
    assert previous is not None
    assert previous[0] == "2026-10-23"


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        # Real restatements from the 21 and 22 Oct replies.
        (
            "That OTP fix is a security fix with no Jira bug. Raise a bug with steps, expected, actual, the root cause and the fix.",
            "Still, please log each as a Jira Bug linked to EIT-8403, with the root cause and fix, so we can see where defects come from.",
            True,
        ),
        (
            "Four repos are pushed with no PRs. Keep the OTP fix in its own commit.",
            "For tomorrow, raise the OTP bug and open PRs for the four repos.",
            True,
        ),
        # Different asks that share a word or two.
        (
            "EIT-8372: run /microservice_review in dry-run on the utility-service changes before the PR",
            "With 31 commits on portal-service-v4 alone, how will he review it in pieces?",
            False,
        ),
        ("Raise a Jira bug for the OTP fix", "Share the branch list with Rahul", False),
        ("", "anything", False),
    ],
)
def test_ask_restatement_detection(a: str, b: str, same: bool) -> None:
    """TC-REP-006: Reworded repeats of an ask are recognised, and different asks are kept apart.

    Given: real restated asks from Priya's replies and unrelated pairs.
    When: is_same_ask() runs (distinctive-word overlap, URLs ignored).
    Then: restatements match and unrelated asks do not.
    """
    assert is_same_ask(a, b) is same
    assert similarity("", "x") == (0.0, 0)
