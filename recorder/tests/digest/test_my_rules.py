"""My rules (free-text rules enforced in code) and hidden sessions."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from tests.cloud.fake_tables import FakeTables
from tests.conftest import IST
from tests.digest.test_generation import DAY, NOW, _make, seeded  # noqa: F401 - fixture
from workdigest.cloud.sync import sync_user_state
from workdigest.digest.draft import render
from workdigest.digest.my_rules import (
    PrRef,
    RuleFilters,
    active_filters,
    hidden_session_keys,
    interpret,
    interpret_with_patterns,
    rule_context,
)
from workdigest.digest.service import DigestContext, prepare_inputs
from workdigest.llm.base import LLMError, Prompt
from workdigest.llm.fake import FakeProvider
from workdigest.report.today import day_evidence
from workdigest.store.db import Store

REPOS = ["quiz-service", "billing-service", "identity-service", "portal-service-v4"]


def _pr(
    store: Store,
    repo: str,
    number: int,
    branch: str,
    opened: datetime,
    *,
    tickets: list[str] | None = None,
) -> None:
    store.conn.execute(
        "INSERT INTO pull_requests (repo, pr_id, title, url, source_branch, dest_branch, state, created_at, "
        "updated_at, ticket_keys, synced_at) VALUES (?, ?, ?, ?, ?, 'development', 'OPEN', ?, ?, ?, ?)",
        (
            repo,
            number,
            f"{repo} change {number}",
            f"https://bitbucket.org/ei-india-admin/{repo}/pull-requests/{number}",
            branch,
            opened.isoformat(),
            opened.isoformat(),
            json.dumps(tickets or []),
            opened.isoformat(),
        ),
    )


def _rule(store: Store, rule_id: str, filters: RuleFilters, status: str = "active") -> None:
    store.conn.execute(
        "INSERT INTO my_rules (rule_id, text, status, params, updated_at) VALUES (?, ?, ?, ?, ?)",
        (rule_id, "rule", status, filters.model_dump_json(), NOW.isoformat()),
    )


@pytest.fixture
def with_prs(seeded: DigestContext) -> DigestContext:  # noqa: F811 - pytest fixture injection
    store = seeded.store
    old = NOW - timedelta(days=150)
    for number in range(3, 8):
        _pr(store, "quiz-service", number, f"EIT-495-part-{number - 2}", old, tickets=["EIT-495"])
    _pr(
        store,
        "identity-service",
        312,
        "feature/browser-close",
        NOW - timedelta(days=20),
        tickets=["EIT-6685"],
    )
    _pr(
        store,
        "billing-service",
        9,
        "feat/billing-migrator",
        NOW - timedelta(days=1),
        tickets=["EIT-8403"],
    )
    return seeded


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Don't include quiz-service PRs 3-7 in my digest",
            RuleFilters(exclude_prs=[PrRef(repo="quiz-service", numbers=[3, 4, 5, 6, 7])]),
        ),
        ("Hide PRs open more than 30 days", RuleFilters(exclude_prs_older_than_days=30)),
        ("Skip PRs older than 2 weeks", RuleFilters(exclude_prs_older_than_days=14)),
        (
            "Never mention identity-service PR #312",
            RuleFilters(exclude_prs=[PrRef(repo="identity-service", numbers=[312])]),
        ),
        ("Leave EIT-495 out", RuleFilters(exclude_tickets=["EIT-495"])),
        (
            "Exclude the portal-service-v4 repo",
            RuleFilters(exclude_repos=["portal-service-v4"]),
        ),
        (
            "Keep In short under 4 sentences",
            RuleFilters(instruction="Keep In short under 4 sentences"),
        ),
    ],
)
def test_rules_read_without_llm(text: str, expected: RuleFilters) -> None:
    """TC-MYR-001: Typed rules are read into filters by patterns when there is no LLM.

    Given: rules about PR ranges, single PRs, ages (days/weeks), tickets, a repo, and free text.
    When: interpreted without an LLM against the known repos.
    Then: each becomes the matching filter; text that is not a filter becomes an instruction.
    """
    assert interpret(text, REPOS, [], None) == expected


def test_llm_reading_is_checked() -> None:
    """TC-MYR-002: What the LLM reads is checked: invented repos are dropped, and a failure falls back to patterns.

    Given: an LLM answer naming a real and an invented repo, then an LLM that fails.
    When: a rule is interpreted.
    Then: only the known repo survives; on failure the pattern reading is used.
    """
    answer = {
        "exclude_prs": [
            {"repo": "quiz-service", "numbers": [3, 4]},
            {"repo": "made-up", "numbers": [1]},
        ],
        "exclude_repos": ["made-up", "identity-service"],
        "exclude_tickets": ["eit-495", "not a key"],
    }
    llm = FakeProvider(lambda _p: json.dumps(answer))
    result = interpret("hide the old attempt stack", REPOS, ["quiz-service #3 ..."], llm)
    assert result.exclude_prs == [PrRef(repo="quiz-service", numbers=[3, 4])]
    assert result.exclude_repos == ["identity-service"]
    assert result.exclude_tickets == ["EIT-495"]

    def broken(_p: Prompt) -> str:
        raise LLMError("cursor-agent is not logged in")

    assert (
        interpret(
            "Hide PRs open more than 30 days", REPOS, [], FakeProvider(broken)
        ).exclude_prs_older_than_days
        == 30
    )
    # A backwards "range" is read as the two PRs named, never as everything in between.
    assert interpret_with_patterns("Hide PR 12 to 9", REPOS).exclude_prs == [PrRef(numbers=[9, 12])]


def test_merge_and_describe() -> None:
    """TC-MYR-003: Active rules merge (strictest age wins) and describe themselves as "Understood as" chips.

    Given: two rules: an quiz-service PR range with a 60-day limit, and a ticket with a 30-day limit and an instruction.
    When: merged and described.
    Then: both exclusions apply, the age limit is 30, and the chips read naturally.
    """
    merged = RuleFilters.merge(
        [
            RuleFilters(
                exclude_prs=[PrRef(repo="quiz-service", numbers=[3, 4])],
                exclude_prs_older_than_days=60,
            ),
            RuleFilters(
                exclude_tickets=["EIT-495"], exclude_prs_older_than_days=30, instruction="Be brief"
            ),
        ]
    )
    assert merged.exclude_prs_older_than_days == 30
    assert merged.describe() == [
        "Hide quiz-service PRs #3, #4",
        "Hide PRs open more than 30 days",
        "Hide ticket EIT-495",
        "Instruction for the AI: Be brief",
    ]
    now = datetime(2026, 10, 26, tzinfo=IST)
    assert merged.excludes_pr("quiz-service", 4, now, now)
    assert merged.excludes_pr("identity-service", 1, now - timedelta(days=31), now)
    assert not merged.excludes_pr("identity-service", 1, now - timedelta(days=29), now)
    assert merged.excludes_pr("identity-service", 2, now, now, ["EIT-495"])
    assert RuleFilters().is_empty()


def test_pr_rule_removes_prs_everywhere(with_prs: DigestContext) -> None:
    """TC-MYR-004: An active PR rule removes those PRs from the evidence, the prompt, the PR section and the branch list.

    Given: PRs quiz-service #3-#7 (150 days old), identity-service #312 and billing-service #9 (whose branch has
           today's commits), and active rules hiding quiz-service #3-#7 and billing-service #9.
    When: the draft is generated.
    Then: none of those PRs appear in the evidence or the LLM prompt or the digest; identity-service #312 still does;
          and feat/billing-migrator is not re-listed as a "branch with no PR".
    """
    store = with_prs.store
    _rule(
        store,
        "r1",
        RuleFilters(exclude_prs=[PrRef(repo="quiz-service", numbers=[3, 4, 5, 6, 7])]),
    )
    _rule(store, "r2", RuleFilters(exclude_prs=[PrRef(repo="billing-service", numbers=[9])]))
    _rule(
        store, "r3", RuleFilters(exclude_tickets=["EIT-6685"]), status="needs_review"
    )  # not confirmed: ignored
    draft, fake = _make(with_prs)
    prompt = next(p.user for p in fake.prompts if "Evidence:" in p.user)
    assert "quiz-service PR #" not in prompt
    assert "billing-service PR #9" not in prompt
    assert "identity-service PR #312" in prompt
    text = render(draft)
    assert "quiz-service PR #3" not in text
    assert "identity-service PR #312" in text
    assert "feat/billing-migrator" not in text.split("8. PRs")[1].split("9.")[0]


def test_age_ticket_repo_rules_and_instruction(with_prs: DigestContext) -> None:
    """TC-MYR-005: Age, ticket and repo rules filter evidence; instructions reach the prompt.

    Given: active rules: PRs older than 30 days, ticket EIT-8403, repo portal-service-v4, and an instruction.
    When: the draft inputs are prepared.
    Then: only PRs younger than 30 days that are not about EIT-8403 remain (identity-service #312), EIT-8403 and its
          commits are gone, and the instruction is in the prompt as the engineer's own instruction.
    """
    store = with_prs.store
    _rule(store, "age", RuleFilters(exclude_prs_older_than_days=30))
    _rule(
        store,
        "ticket",
        RuleFilters(exclude_tickets=["EIT-8403"], instruction="Keep In short under 4 sentences"),
    )
    _rule(store, "repo", RuleFilters(exclude_repos=["portal-service-v4"]))
    inputs = prepare_inputs(with_prs, DAY, llm=None, now=NOW)
    # quiz-service #3-#7 are too old, billing-service #9 is about EIT-8403: only identity-service #312 stays.
    assert [i.refs["url"].rsplit("/", 1)[1] for i in inputs.pack.of_kind("pull_request")] == ["312"]
    assert "EIT-8403" not in inputs.pack.tickets
    assert not inputs.pack.of_kind("commit")
    assert inputs.instructions == "Keep In short under 4 sentences"
    from workdigest.digest.generator import build_prompt

    assert "Engineer's own instructions" in build_prompt(inputs).user
    assert active_filters(store).exclude_repos == ["portal-service-v4"]


def test_hidden_session_is_left_out_and_restorable(seeded: DigestContext) -> None:  # noqa: F811
    """TC-MYR-006: A hidden session leaves the evidence, test runs, tickets and day counts, and comes back when restored.

    Given: the seeded day with session S1 (prompts about EIT-8403 and a 650/650 test run).
    When: S1 is hidden, then restored.
    Then: while hidden, day_evidence has no session and no test run, the prompt-ticket count drops, and
          the summarizer skips it; after restore everything is back.
    """
    store = seeded.store
    before = day_evidence(store, DAY.isoformat(), NOW, 30)
    assert before.sessions
    assert before.test_runs
    store.conn.execute("INSERT INTO hidden_sessions VALUES ('claude:S1', ?)", (NOW.isoformat(),))
    assert hidden_session_keys(store) == {"claude:S1"}
    hidden = day_evidence(store, DAY.isoformat(), NOW, 30)
    assert hidden.sessions == []
    assert hidden.test_runs == []
    inputs = prepare_inputs(seeded, DAY, llm=None, now=NOW)
    assert not inputs.pack.of_kind("session")
    assert not inputs.pack.of_kind("test_run")
    store.conn.execute("DELETE FROM hidden_sessions")
    assert day_evidence(store, DAY.isoformat(), NOW, 30).sessions


def test_sync_user_state(store: Store) -> None:
    """TC-MYR-007: Each tick mirrors hidden sessions and My rules, and reads new rules for confirmation.

    Given: a cloud with one hidden session, a new rule ("parsing"), an active rule, and a rule the user
           re-edited while the Mac was reading it.
    When: sync_user_state runs, then the hidden session is restored and the active rule deleted in the app.
    Then: the new rule becomes needs_review with its filters in the cloud and locally; the re-edited rule
          stays parsing; local tables mirror the cloud, including the restore and the deletion.
    """
    tables = FakeTables()
    tables.rows["hidden_sessions"] = [
        {"session_key": "claude:P1", "hidden_at": "2026-10-26T10:00:00+05:30"}
    ]
    tables.rows["standing_rules"] = [
        {
            "rule_id": "new",
            "text": "Hide PRs open more than 30 days",
            "source": "mine",
            "status": "parsing",
        },
        {
            "rule_id": "on",
            "text": "Leave EIT-495 out",
            "source": "mine",
            "status": "active",
            "check_params": {"exclude_tickets": ["EIT-495"]},
        },
        {"rule_id": "seed", "text": "Follow the 11 points", "source": "seed", "status": "active"},
    ]

    def reader(text: str) -> RuleFilters:
        if text.startswith("Edited"):
            raise AssertionError("not reached")
        return interpret(text, REPOS, [], None)

    report = sync_user_state(store, tables, reader)
    assert report.rules_read == ["new"]
    cloud_new = next(r for r in tables.rows["standing_rules"] if r["rule_id"] == "new")
    assert cloud_new["status"] == "needs_review"
    assert cloud_new["check_params"]["exclude_prs_older_than_days"] == 30
    assert hidden_session_keys(store) == {"claude:P1"}
    assert active_filters(store).exclude_tickets == ["EIT-495"]
    assert store.scalar("SELECT status FROM my_rules WHERE rule_id = 'new'") == "needs_review"

    # The user edits a rule again while the Mac is reading it: the reading must not overwrite the edit.
    tables.rows["standing_rules"][0].update(status="parsing", updated_at="t1")

    def racing(text: str) -> RuleFilters:
        tables.rows["standing_rules"][0].update(
            text="Hide PRs open more than 60 days", updated_at="t2"
        )
        return interpret(text, REPOS, [], None)

    raced = sync_user_state(store, tables, racing)
    assert raced.rules_read == []
    assert tables.rows["standing_rules"][0]["status"] == "parsing"

    tables.rows["hidden_sessions"] = []
    tables.rows["standing_rules"] = [
        r for r in tables.rows["standing_rules"] if r["rule_id"] != "on"
    ]
    sync_user_state(store, tables, reader)
    assert hidden_session_keys(store) == set()
    assert active_filters(store).exclude_tickets == []


def test_rule_context_lists_repos_and_prs(with_prs: DigestContext) -> None:
    """TC-MYR-008: The rule reader gets the known repos and one line per PR, so rules can name them.

    Given: configured repos and synced PRs.
    When: rule_context is built.
    Then: repos from config, the repo table and PRs are listed once each, and PR lines include number and title.
    """
    repos, prs = rule_context(with_prs.store, ["portal-service-v4"])
    assert {"quiz-service", "billing-service", "identity-service", "portal-service-v4"} <= set(
        repos
    )
    assert any(line.startswith('quiz-service #3 "quiz-service change 3" OPEN') for line in prs)


def test_cleared_priya_rules_and_asks(
    seeded: DigestContext,  # noqa: F811 - pytest fixture injection
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-MYR-009: Clearing Priya's rules and open asks in the app stops them everywhere on the Mac.

    Given: the seed rules and the open OTP ask; the app switched rule R10 off and cleared the OTP ask.
    When: the Mac syncs, re-seeds its rules, and Priya later repeats the OTP request.
    Then: R10 stays off (not checked, not re-enabled by seeding); the cleared ask is never reported and
          has no reminder; the repeated request becomes a new open ask instead of bumping the cleared one.
    """
    from workdigest.digest.repository import DigestRepository
    from workdigest.digest.service import add_reply

    store = seeded.store
    repo = DigestRepository(store)
    repo.seed_rules()
    tables = FakeTables()
    tables.rows["standing_rules"] = [
        {"rule_id": "R10-send-time", "source": "seed", "active": False, "updated_by": "app"},
        {"rule_id": "R01-structure", "source": "seed", "active": True, "updated_by": "mac"},
    ]
    sync_user_state(store, tables, lambda text: interpret(text, REPOS, [], None))
    repo.seed_rules()
    active = {r.rule_id for r in repo.rules()}
    assert "R10-send-time" not in active
    assert "R01-structure" in active

    otp = next(a for a in repo.asks(status="open") if "OTP" in a.text)
    monkeypatch.setattr("workdigest.digest.repository.now_local", lambda: NOW)
    repo.set_ask_status(otp.ask_id, "cleared")
    inputs = prepare_inputs(seeded, DAY, llm=None, now=NOW)
    assert inputs.pack.of_kind("ask") == []
    assert not [a for a in inputs.open_asks if "OTP" in a.text]
    # A new reply (different wording; an identical reply is de-duplicated as the same message).
    add_reply(
        store,
        seeded.config,
        "Raise the Jira bug for the OTP fix, it is still missing.",
        DAY,
        None,
        confirm=True,
    )
    reopened = [a for a in repo.asks(status="open") if "OTP" in a.text]
    assert len(reopened) == 1
    assert reopened[0].ask_id != otp.ask_id
    assert reopened[0].times_raised == 1
