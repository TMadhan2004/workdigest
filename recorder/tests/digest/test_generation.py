"""End-to-end draft generation with a fake LLM, grounding, compliance and WhatsApp output."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import IST, ME, claude_payload, spool_line
from workdigest.config import Config
from workdigest.digest.draft import Draft, EvidenceItem, Statement, render, whatsapp_safe
from workdigest.digest.grounding import ground_statement
from workdigest.digest.repository import DigestRepository
from workdigest.digest.service import (
    DigestContext,
    add_reply,
    close_day,
    create_draft,
    draft_prompt_text,
    prepare_inputs,
    regenerate,
)
from workdigest.ingest.normalize import EventFactory
from workdigest.ingest.spool import ingest_spool
from workdigest.llm.base import LLMError, Prompt
from workdigest.llm.fake import FakeProvider
from workdigest.models import Kind, Origin, Source
from workdigest.paths import Paths
from workdigest.store.db import Store

DAY = date(2026, 10, 26)  # Monday
T0 = datetime(2026, 10, 26, 10, 0, tzinfo=IST)
NOW = datetime(2026, 10, 26, 18, 0, tzinfo=IST)
SECRET = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2"


@pytest.fixture
def seeded(store: Store, factory: EventFactory, paths: Paths, config: Config) -> DigestContext:
    """A realistic Monday: one Claude session with prompts and a test run, two commits, a plan and an ask."""
    lines = [
        spool_line(
            claude_payload(
                "UserPromptSubmit",
                session="S1",
                prompt=f"Finish EIT-8403 billing move, token {SECRET}",
            ),
            T0,
        ),
        spool_line(
            claude_payload("UserPromptSubmit", session="S1", prompt="Now run the EIT-8403 tests"),
            T0 + timedelta(minutes=5),
        ),
        spool_line(
            claude_payload(
                "PostToolUse",
                session="S1",
                tool_name="Bash",
                tool_use_id="t-full",
                tool_input={"command": "npm test"},
                tool_response={"stdout": "      Tests  650 passed (650)\n", "stderr": ""},
            ),
            T0 + timedelta(minutes=20),
        ),
        spool_line(
            claude_payload(
                "PostToolUse",
                session="S1",
                tool_name="Bash",
                tool_use_id="t-subset",
                tool_input={"command": "npm test -- billing"},
                tool_response={"stdout": "      Tests  25 passed (25)\n", "stderr": ""},
            ),
            T0 + timedelta(minutes=40),
        ),
        spool_line(
            claude_payload(
                "Stop",
                session="S1",
                last_assistant_message="Copied 2,428 billing profiles; 650/650 tests pass.",
            ),
            T0 + timedelta(hours=1),
        ),
    ]
    (paths.spool_dir / "2026-10-26.jsonl").write_text("\n".join(lines) + "\n")
    ingest_spool(store, factory, paths.spool_dir, today=DAY)
    store.upsert_repo(
        "billing-service",
        "/w/billing-service",
        "git@bitbucket.org:ei-india-admin/billing-service.git",
        "https://bitbucket.org/ei-india-admin/billing-service",
    )
    for index, (sha, subject) in enumerate(
        (
            ("a" * 40, "feat(billing): EIT-8403 load legacy account data"),
            ("b" * 40, "fix(billing): hide card fingerprint"),
        )
    ):
        ts = T0 + timedelta(hours=2, minutes=index)
        store.upsert_commit(
            {
                "repo": "billing-service",
                "sha": sha,
                "author_name": "Arjun K",
                "author_email": ME,
                "authored_at": ts.isoformat(),
                "committed_at": ts.isoformat(),
                "work_date": DAY.isoformat(),
                "subject": subject,
                "branches": '["feat/billing-migrator"]',
                "pushed": 1,
                "files_changed": 3,
                "insertions": 40,
                "deletions": 2,
                "ticket_keys": '["EIT-8403"]',
            }
        )
        store.insert_event(
            factory.build(
                source=Source.GIT,
                origin=Origin.POLLER,
                kind=Kind.COMMIT,
                natural_key=sha,
                ts=ts,
                payload={"sha": sha, "subject": subject},
                repo="billing-service",
                branch="feat/billing-migrator",
                ticket_texts=(subject,),
            )
        )
    repo = DigestRepository(store)
    repo.save_plan(
        date(2026, 10, 23),
        ["Finish the EIT-8403 legacy copy", "Review the Excel test sheets with Rahul"],
    )
    add_reply(
        store,
        config,
        "Please raise a Jira bug for the OTP fix.",
        date(2026, 10, 23),
        None,
        confirm=True,
    )
    return DigestContext(store, config, paths)


def _evidence_ids(prompt: Prompt, prefix: str) -> list[str]:
    return sorted(
        {
            line.split(":")[0]
            for line in prompt.user.splitlines()
            if line.startswith(prefix) and line[1:2].isdigit()
        }
    )


def _responder(answer: dict[str, Any]):  # type: ignore[no-untyped-def]
    def respond(prompt: Prompt) -> str:
        if "summarize one AI coding-agent session" in prompt.system:
            return json.dumps(
                {
                    "headline": "Billing move",
                    "done": ["Copied 2,428 billing profiles"],
                    "tests": ["650/650"],
                }
            )
        return json.dumps(answer)

    return respond


GOOD_ANSWER = {
    "in_short": [
        {
            "text": "Billing data loading for EIT-8403 is committed and pushed in billing-service.",
            "evidence_ids": ["K1", "C1", "C2"],
        },
        {"text": "The billing-service suite passes 650/650.", "evidence_ids": ["T1"]},
        {"text": "The UI suite passes 999/999.", "evidence_ids": ["T1"]},
    ],
    "sections": {
        "done_today": [
            {
                "text": "EIT-8403 – Legacy account data in Billing",
                "evidence_ids": ["K1"],
                "children": [
                    {
                        "text": "Loaded legacy account data and hid the card fingerprint in reports (2 commits).",
                        "evidence_ids": ["C1", "C2"],
                    },
                    {"text": "Copied 2,428 billing profiles.", "evidence_ids": ["S1"]},
                ],
            }
        ],
        "plan_vs_actual": [
            {
                "text": "Finish the EIT-8403 legacy copy → Done: committed and pushed.",
                "evidence_ids": ["P1", "C1"],
            },
            {
                "text": "Review the Excel test sheets with Rahul → Not started.",
                "evidence_ids": ["P2"],
            },
        ],
        "schedule": [{"text": "On schedule: EIT-8403.", "evidence_ids": ["K1"]}],
        "blockers": [{"text": "None.", "evidence_ids": []}],
        "risks": [
            {
                "text": "Real-tenant account data not yet checked on staging – owner: me, by 28 Oct.",
                "evidence_ids": ["S1"],
            }
        ],
        "decisions": [{"text": "Hide card fingerprints in reports.", "evidence_ids": ["C2"]}],
        "learnings": [
            {"text": "Reports can leak sensitive data, even as hashes.", "evidence_ids": ["C2"]}
        ],
        "ai_tools": [
            {
                "text": "Claude Code ran the migration work and the test suite.",
                "evidence_ids": ["S1", "T1"],
            }
        ],
        "plan_upcoming": [
            {"text": "Check account data on staging by 28 Oct (me).", "evidence_ids": []}
        ],
    },
    "extra_not_in_plan": "the card fingerprint fix.",
    "closing": [
        {"text": "On the OTP fix: I will raise the Jira Bug today.", "evidence_ids": ["A1"]}
    ],
}


def _make(
    seeded: DigestContext, answer: dict[str, Any] | None = None
) -> tuple[Draft, FakeProvider]:
    fake = FakeProvider(_responder(answer or GOOD_ANSWER))
    _, draft = create_draft(seeded, DAY, llm=fake, now=NOW)
    return draft, fake


def test_full_draft_is_grounded_and_whatsapp_formatted(seeded: DigestContext) -> None:
    """TC-GEN-001: A full draft has the fixed header, factual testing/commits and the 11 numbered sections.

    Given: a seeded Monday and a fake LLM returning a well-formed answer.
    When: create_draft() runs.
    Then: the text starts with "Greetings Priya,", lists EIT-8403 with its Jira URL, has sections 1–11 in
          order, reports the full suite (650/650), not the later 25/25 subset, links the
          Bitbucket branch, and contains no Markdown (#, **, |).
    """
    draft, _ = _make(seeded)
    text = render(draft)
    assert text.startswith(
        "Greetings Priya,\n\nDaily digest – Mon, 26 Oct 2026\nJira:\n- EIT-8403 – "
    )
    assert "https://ei-study.atlassian.net/browse/EIT-8403" in text
    numbers = [m.group(1) for line in text.splitlines() if (m := re.match(r"^(\d{1,2})\. ", line))]
    assert numbers == [str(n) for n in range(1, 12)]
    assert "unknown-repo vitest: 650/650 tests pass" in text or "vitest: 650/650 tests pass" in text
    assert "25/25" not in text
    assert (
        "https://bitbucket.org/ei-india-admin/billing-service/commits/branch/feat%2Fbilling-migrator"
        in text
    )
    assert "2. Plan vs actual (against the 23 Oct five-day plan, day 2 of 5)" in text
    assert "Extra, not in the plan: the card fingerprint fix." in text
    assert "7. Releases and deployment – None." in text
    for forbidden in ("**", "\n#", "|"):
        assert forbidden not in text


def test_invented_numbers_and_claims_are_flagged(seeded: DigestContext) -> None:
    """TC-GEN-002: A number absent from evidence, or a "done" backed only by an agent's claim, is marked unverified.

    Given: the fake LLM writes "UI suite passes 999/999" citing T1, and "Copied 2,428 billing profiles" citing only S1.
    When: the draft is generated.
    Then: the 999 line is rendered with [unverified: confirm number 999] and the 2,428 line with an
          "only an agent's report" note; both appear as unverified gaps; correctly cited lines stay verified.
    """
    draft, _ = _make(seeded)
    text = render(draft)
    assert "The UI suite passes 999/999. [unverified: confirm number 999]" in text
    assert (
        "Copied 2,428 billing profiles. [unverified: confirm only an agent's report backs this"
        in text
    )
    unverified = [g.message for g in draft.gaps if g.kind == "unverified"]
    assert any("999" in m for m in unverified)
    assert any("2,428" in m for m in unverified)
    statements = {s.text: s for _, s in draft.all_statements()}
    assert statements["The billing-service suite passes 650/650."].verified
    assert statements[
        "Real-tenant account data not yet checked on staging – owner: me, by 28 Oct."
    ].verified


def test_gap_panel_contents(seeded: DigestContext) -> None:
    """TC-GEN-003: The gap panel lists the rating, PR turnaround, atomic-commit result, the real-action ask and send time.

    Given: no rating recorded, an open "raise a Jira bug" ask, and generation at 18:00.
    When: the draft is generated.
    Then: there are input gaps for the day rating and PR links, a rule gap for R04 atomic-commit, a
          real_action gap for the OTP bug, and no R10 send-time gap (more than an hour before 21:00).
    """
    draft, _ = _make(seeded)
    messages = [(g.kind, g.ref or "", g.message) for g in draft.gaps]
    assert any(k == "input" and "day rating" in m for k, _, m in messages)
    assert any(k == "input" and "PR links" in m for k, _, m in messages)
    assert any(k == "rule" and r == "R04-atomic-commit" for k, r, _ in messages)
    assert any(k == "real_action" and "OTP" in m for k, _, m in messages)
    assert not any(r == "R10-send-time" for _, r, _ in messages)


def test_send_deadline_gap_near_nine_pm(seeded: DigestContext) -> None:
    """TC-GEN-004: Within an hour of the 21:00 deadline the draft warns to send it.

    Given: generation at 20:15 IST on the work day.
    When: the draft is generated.
    Then: there is an R10-send-time gap saying the deadline is close.
    """
    fake = FakeProvider(_responder(GOOD_ANSWER))
    _, draft = create_draft(seeded, DAY, llm=fake, now=NOW.replace(hour=20, minute=15))
    assert any(g.ref == "R10-send-time" and "is close" in g.message for g in draft.gaps)


def test_rating_project_team_from_close_day(seeded: DigestContext) -> None:
    """TC-GEN-005: `close` values fill the header, and the rating gap disappears.

    Given: close_day(rating=7, project=..., team=[Rahul, Vikram]).
    When: the draft is generated.
    Then: the header shows "Day rating: 7", the project and the team, with no rating gap.
    """
    close_day(
        seeded.store,
        DAY,
        rating=7,
        project="Platform Consolidation",
        team=["Rahul", "Vikram"],
    )
    draft, _ = _make(seeded)
    text = render(draft)
    assert "Day rating: 7" in text
    assert "Project: Platform Consolidation" in text
    assert "Team members: Rahul, Vikram" in text
    assert not any("day rating" in g.message for g in draft.gaps)
    with pytest.raises(ValueError, match="1, 3, 7 or 9"):
        close_day(seeded.store, DAY, rating=5)


def test_no_llm_mode_never_invents(seeded: DigestContext) -> None:
    """TC-GEN-006: Without an LLM, facts are still produced and narrative sections become visible placeholders.

    Given: provider none (llm=None).
    When: create_draft() runs.
    Then: the Testing and Commits facts are present, narrative sections show "[missing: …]", the In short is
          a placeholder, and each empty section has a "missing" gap.
    """
    _, draft = create_draft(seeded, DAY, llm=None, now=NOW)
    text = render(draft)
    assert "650/650" in text
    assert "In short: [missing: In short]" in text
    assert "[missing: no evidence captured for this section]" in text
    missing = {g.section for g in draft.gaps if g.kind == "missing"}
    assert {"learnings", "decisions", "risks"} <= missing
    assert draft.provider == "none"


def test_llm_failure_degrades_to_no_llm(seeded: DigestContext) -> None:
    """TC-GEN-007: An LLM that fails produces a usable factual draft and an explicit gap, not a crash.

    Given: a provider that raises LLMError on every call.
    When: create_draft() runs.
    Then: a draft is returned with an "LLM unavailable" input gap and the testing facts intact.
    """

    class Failing(FakeProvider):
        def complete(self, prompt: Prompt) -> str:
            raise LLMError("cursor_cli: not logged in")

    _, draft = create_draft(seeded, DAY, llm=Failing([]), now=NOW)
    assert any("LLM unavailable" in g.message for g in draft.gaps)
    assert "650/650" in render(draft)


def test_what_is_sent_is_redacted_and_minimal(seeded: DigestContext) -> None:
    """TC-GEN-008: The LLM input contains redacted excerpts only, with no secrets, raw transcripts or diffs.

    Given: a prompt in the session that contained a GitHub token.
    When: the draft prompt (`--show-input`) and the summarizer prompts are inspected.
    Then: the token never appears, its redaction marker does, and no prompt contains "diff --git".
    """
    draft_prompt = draft_prompt_text(prepare_inputs(seeded, DAY, llm=None, now=NOW))
    _, fake = _make(seeded)
    every_prompt = [draft_prompt] + [p.system + p.user for p in fake.prompts]
    for text in every_prompt:
        assert SECRET not in text
        assert "diff --git" not in text
    assert any("[REDACTED:github_token]" in p.user for p in fake.prompts)


def test_transmit_flags_remove_categories(seeded: DigestContext) -> None:
    """TC-GEN-009: Turning off prompt excerpts and commit subjects removes them from what is sent.

    Given: transmit.prompt_excerpts = false and transmit.commit_subjects = false.
    When: a draft is generated with a recording fake LLM.
    Then: no prompt text "Now run the EIT-8403 tests" and no commit subject reaches the LLM.
    """
    config = Config.model_validate(
        {
            **seeded.config.model_dump(mode="json"),
            "transmit": {"prompt_excerpts": False, "commit_subjects": False},
        }
    )
    ctx = DigestContext(seeded.store, config, seeded.paths)
    fake = FakeProvider(_responder(GOOD_ANSWER))
    create_draft(ctx, DAY, llm=fake, now=NOW)
    sent = "\n".join(p.user for p in fake.prompts)
    assert "Now run the EIT-8403 tests" not in sent
    assert "load legacy account data" not in sent
    assert "(subject withheld)" in sent


def test_llm_disabled_flag_means_no_provider(seeded: DigestContext) -> None:
    """TC-GEN-010: transmit.llm_enabled = false disables every provider.

    Given: llm_enabled false.
    When: make_provider() runs for cursor_cli.
    Then: it returns None.
    """
    from workdigest.digest.service import make_provider

    config = Config.model_validate({"transmit": {"llm_enabled": False}})
    assert make_provider(config, "cursor_cli") is None
    assert make_provider(Config(), "none") is None
    assert make_provider(Config(), "claude_cli") is not None
    with pytest.raises(ValueError, match="unknown provider"):
        make_provider(Config(), "gpt")


def test_summaries_are_cached(seeded: DigestContext) -> None:
    """TC-GEN-011: An unchanged session is not re-summarized on the next draft.

    Given: two consecutive drafts on the same unchanged evidence.
    When: the fake LLM's prompts are counted.
    Then: the summarizer is called once in total (the second run uses the cache).
    """
    fake = FakeProvider(_responder(GOOD_ANSWER))
    create_draft(seeded, DAY, llm=fake, now=NOW)
    create_draft(seeded, DAY, llm=fake, now=NOW)
    summarizer_calls = [
        p for p in fake.prompts if "summarize one AI coding-agent session" in p.system
    ]
    assert len(summarizer_calls) == 1


def test_regenerate_one_section_only(seeded: DigestContext) -> None:
    """TC-GEN-012: Regenerating "learnings" changes only that section.

    Given: a stored draft.
    When: regenerate("learnings") runs with a new answer.
    Then: learnings has the new text and every other section's text is unchanged; Testing/Commits are kept
          when regenerating done_today; code-owned sections cannot be regenerated.
    """
    draft, _ = _make(seeded)
    before = {
        s.key: render(Draft(**{**draft.model_dump(), "sections": [s]})) for s in draft.sections
    }
    fake = FakeProvider(
        ['{"statements": [{"text": "Fakes hide integration bugs.", "evidence_ids": ["T1"]}]}']
    )
    _, updated = regenerate(seeded, DAY, "learnings", llm=fake, now=NOW)
    learnings = updated.section("learnings")
    assert learnings is not None
    assert [s.text for s in learnings.statements] == ["Fakes hide integration bugs."]
    for section in updated.sections:
        if section.key != "learnings":
            assert (
                render(Draft(**{**updated.model_dump(), "sections": [section]}))
                == before[section.key]
            ), section.key
    fake2 = FakeProvider(
        ['{"statements": [{"text": "EIT-8403 – rewritten", "evidence_ids": ["K1"]}]}']
    )
    _, again = regenerate(seeded, DAY, "done_today", llm=fake2, now=NOW)
    done = again.section("done_today")
    assert done is not None
    assert [s.text for s in done.statements][-2:] == ["Testing", "Commits"]
    with pytest.raises(ValueError, match="written by code"):
        regenerate(seeded, DAY, "prs", llm=fake2, now=NOW)


def test_regenerate_without_draft(seeded: DigestContext) -> None:
    """TC-GEN-013: Regenerating before any draft exists is a clear error.

    Given: no stored draft for 27 Oct.
    When: regenerate() runs.
    Then: LookupError mentions `digest draft`.
    """
    with pytest.raises(LookupError, match="digest draft"):
        regenerate(seeded, date(2026, 10, 27), "risks", llm=FakeProvider([]), now=NOW)


def test_draft_is_stored_with_statements(seeded: DigestContext) -> None:
    """TC-GEN-014: Each draft is stored with per-statement evidence links (for the app's evidence drawer).

    Given: a generated draft.
    When: the digests and digest_statements tables are read.
    Then: one generated draft exists, statements reference evidence ids, and unverified ones carry problems.
    """
    _make(seeded)
    store = seeded.store
    assert (
        store.scalar("SELECT COUNT(*) FROM digests WHERE origin = 'generated' AND status = 'draft'")
        == 1
    )
    rows = store.query("SELECT text, verified, evidence_ids, problems FROM digest_statements")
    assert any(json.loads(r["evidence_ids"]) for r in rows)
    assert any(not r["verified"] and json.loads(r["problems"]) for r in rows)


def test_closing_reports_only_answered_asks(
    seeded: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-GEN-027: The closing reports asks answered since the last digest, Done with evidence and Disagree with the reason; pending asks never appear.

    Given: the open ask "Please raise a Jira bug for the OTP fix." plus two more: one marked Done with a link
           and one marked Disagree with a reason, both closed on the draft day.
    When: the draft is generated (the model also tries to write a closing).
    Then: the closing has exactly the two answered asks, worded by code; the pending OTP ask is not in
          the message and is not sent to the model; the model's closing is ignored.
    """
    repo = DigestRepository(seeded.store)
    for ask_id, text in (
        ("q-crew", "Do you have a crew of at least 10 agents?"),
        ("a-branch", "Share the branch list with Rahul"),
    ):
        seeded.store.conn.execute(
            "INSERT INTO asks (ask_id, text, kind, opened_on) VALUES (?, ?, 'one_time_action', '2026-10-23')",
            (ask_id, text),
        )
    monkeypatch.setattr("workdigest.digest.repository.now_local", lambda: NOW)
    repo.set_ask_status(
        "a-branch", "done", evidence="https://bitbucket.org/ei-india-admin/x/branches"
    )
    repo.set_ask_status(
        "q-crew", "disagree", reason="3 agents cover my work; 10 would add review overhead"
    )
    answer = json.loads(json.dumps(GOOD_ANSWER))
    answer["closing"] = [{"text": "The OTP bug is still to be raised.", "evidence_ids": []}]
    draft, fake = _make(seeded, answer)
    closing = [s.text for s in draft.closing]
    assert closing == [
        "Share the branch list with Rahul: done – https://bitbucket.org/ei-india-admin/x/branches.",
        "Do you have a crew of at least 10 agents: I disagree – 3 agents cover my work; 10 would add review overhead.",
    ]
    assert all(s.verified for s in draft.closing)
    text = render(draft)
    assert "OTP" not in text.split("11. Plan for upcoming days")[1]
    prompt = next(p.user for p in fake.prompts if "Evidence:" in p.user)
    assert "raise a Jira bug for the OTP fix" not in prompt
    assert not any(g.kind == "ask" for g in draft.gaps)


def test_answered_asks_are_reported_once(
    seeded: DigestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-GEN-026: An answered ask appears in the first digest after it was closed, not again later.

    Given: an ask marked Done on 26 Oct and a digest saved for 26 Oct.
    When: the drafts for 26 Oct (again) and 27 Oct are prepared.
    Then: 26 Oct still reports it; 27 Oct does not.
    """
    from workdigest.digest.service import prepare_inputs

    repo = DigestRepository(seeded.store)
    seeded.store.conn.execute(
        "INSERT INTO asks (ask_id, text, kind, opened_on) VALUES ('a1', 'Comment on EIT-8403', 'one_time_action', '2026-10-23')"
    )
    monkeypatch.setattr("workdigest.digest.repository.now_local", lambda: NOW)
    repo.set_ask_status("a1", "done", evidence="https://ei-study.atlassian.net/browse/EIT-8403")
    _make(seeded)  # saves the 26 Oct draft
    again = prepare_inputs(seeded, DAY, llm=None, now=NOW)
    assert [i.refs["ask_id"] for i in again.pack.of_kind("ask")] == ["a1"]
    later = prepare_inputs(seeded, DAY + timedelta(days=1), llm=None, now=NOW + timedelta(days=1))
    assert later.pack.of_kind("ask") == []


def test_rebased_commit_copies_counted_once(store: Store) -> None:
    """TC-GEN-016: Rebased copies (new SHA, same subject and author time) count as one commit on the live branch.

    Given: one commit on backup/x-pre-rebase and its rebased copy on feat/x, plus a distinct commit.
    When: day_evidence() runs.
    Then: two commits are reported; the merged one lists feat/x first and is pushed when either copy is.
    """
    from workdigest.report.today import day_evidence

    base = {
        "repo": "nav",
        "author_name": "M",
        "author_email": ME,
        "committed_at": T0.isoformat(),
        "work_date": DAY.isoformat(),
        "files_changed": 1,
        "insertions": 1,
        "deletions": 0,
        "ticket_keys": "[]",
    }
    store.upsert_commit(
        {
            **base,
            "sha": "1" * 40,
            "authored_at": T0.isoformat(),
            "subject": "feat: a",
            "branches": '["backup/x-pre-rebase"]',
            "pushed": 1,
        }
    )
    store.upsert_commit(
        {
            **base,
            "sha": "2" * 40,
            "authored_at": T0.isoformat(),
            "subject": "feat: a",
            "branches": '["feat/x"]',
            "pushed": 0,
        }
    )
    store.upsert_commit(
        {
            **base,
            "sha": "3" * 40,
            "authored_at": (T0 + timedelta(minutes=1)).isoformat(),
            "subject": "feat: b",
            "branches": '["feat/x"]',
            "pushed": 0,
        }
    )
    commits = day_evidence(store, DAY.isoformat(), NOW, 30).commits
    assert [c.subject for c in commits] == ["feat: a", "feat: b"]
    assert commits[0].branches == ["feat/x", "backup/x-pre-rebase"]
    assert commits[0].pushed


def test_releases_none_is_not_unverified(seeded: DigestContext) -> None:
    """TC-GEN-017: "Releases – None." renders cleanly and becomes a confirm gap, not an [unverified] mark.

    Given: an answer with no releases content.
    When: the draft is generated.
    Then: the line is "7. Releases and deployment – None." and there is a releases input gap.
    """
    draft, _ = _make(seeded)
    text = render(draft)
    assert "7. Releases and deployment – None.\n" in text
    assert any(
        g.section == "releases" and "confirm nothing was deployed" in g.message for g in draft.gaps
    )


# ------------------------------------------------------------------ grounding
EVIDENCE = {
    "C1": EvidenceItem(
        id="C1",
        kind="commit",
        strength="fact",
        text='repo [b] abcdef12 "feat: x" +10/-2',
        refs={"sha": "abcdef1234567890"},
    ),
    "C2": EvidenceItem(
        id="C2",
        kind="commit",
        strength="fact",
        text='repo [b] 1234abcd "fix: y" +1/-1',
        refs={"sha": "1234abcd99"},
    ),
    "T1": EvidenceItem(
        id="T1", kind="test_run", strength="fact", text="repo jest: 644/644 tests passed"
    ),
    "S1": EvidenceItem(
        id="S1", kind="session", strength="claim", text="agent says: migrated 4,014 rows for EIT-1"
    ),
    "K1": EvidenceItem(
        id="K1", kind="ticket", strength="fact", text="EIT-1 – Title", refs={"key": "EIT-1"}
    ),
}


@pytest.mark.parametrize(
    ("statement", "section", "verified", "problem"),
    [
        (Statement(text="Tests pass 644/644.", evidence_ids=["T1"]), "done_today", True, None),
        (
            Statement(text="Tests pass 645/645.", evidence_ids=["T1"]),
            "done_today",
            False,
            "number 645",
        ),
        (Statement(text="Made 2 commits.", evidence_ids=["C1", "C2"]), "done_today", True, None),
        (
            Statement(text="Made 3 commits.", evidence_ids=["C1", "C2"]),
            "done_today",
            False,
            "number 3",
        ),
        (
            Statement(text="Migrated 4,014 rows.", evidence_ids=["S1"]),
            "done_today",
            False,
            "only an agent's report",
        ),
        (Statement(text="Migrated 4,014 rows.", evidence_ids=["S1"]), "learnings", True, None),
        (Statement(text="Fixed EIT-2.", evidence_ids=["K1"]), "done_today", False, "ticket EIT-2"),
        (
            Statement(text="See https://evil.example/x", evidence_ids=["K1"]),
            "done_today",
            False,
            "link",
        ),
        (Statement(text="Commit abcdef12 landed.", evidence_ids=["C1"]), "done_today", True, None),
        (
            Statement(text="Commit deadbeef landed.", evidence_ids=["C1"]),
            "done_today",
            False,
            "commit deadbeef",
        ),
        (Statement(text="Did a thing.", evidence_ids=[]), "done_today", False, "no evidence cited"),
        (
            Statement(text="Did a thing.", evidence_ids=["X9"]),
            "decisions",
            False,
            "unknown evidence X9",
        ),
        (Statement(text="None.", evidence_ids=[]), "blockers", True, None),
        (
            Statement(text="Atomic-commit check: [missing: result]", evidence_ids=[]),
            "done_today",
            True,
            None,
        ),
    ],
)
def test_grounding_rules(
    statement: Statement, section: str, verified: bool, problem: str | None
) -> None:
    """TC-GRD-001: Grounding checks numbers, derived counts, claim-only facts, tickets, links, SHAs and citations.

    Given: statements against a small evidence set (commits, a test run, a session claim, a ticket).
    When: ground_statement() runs.
    Then: only statements fully backed by their cited evidence are verified, and the problem names what to confirm.
    """
    grounded = ground_statement(statement, section, EVIDENCE)
    assert grounded.verified is verified
    if problem:
        assert any(problem in p for p in grounded.problems), grounded.problems


def test_children_are_grounded_independently() -> None:
    """TC-GRD-002: A parent's children are each grounded; a bad child does not taint the parent.

    Given: a ticket headline (K1) with one good child (T1) and one invented child.
    When: grounded.
    Then: the parent and good child are verified, and the invented child is not.
    """
    parent = Statement(
        text="EIT-1 – Title",
        evidence_ids=["K1"],
        children=[
            Statement(text="644/644 pass", evidence_ids=["T1"]),
            Statement(text="700/700 pass", evidence_ids=["T1"]),
        ],
    )
    grounded = ground_statement(parent, "done_today", EVIDENCE)
    assert grounded.verified
    assert [c.verified for c in grounded.children] == [True, False]


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("**Done** today", "*Done* today"),
        ("## Heading\ntext", "Heading\ntext"),
        (
            "see [PR 41](https://bitbucket.org/x/pull-requests/41)",
            "see PR 41 https://bitbucket.org/x/pull-requests/41",
        ),
        ("a | b", "a / b"),
    ],
)
def test_whatsapp_safe(raw: str, clean: str) -> None:
    """TC-GRD-003: Markdown the model might emit is converted to WhatsApp-safe text.

    Given: **bold**, # headings, [text](url) links and table pipes.
    When: whatsapp_safe() runs.
    Then: WhatsApp *bold*, plain lines, "text url" and slashes come back.
    """
    assert whatsapp_safe(raw) == clean


def test_render_round_trips_through_parser(seeded: DigestContext, tmp_path: Path) -> None:
    """TC-GRD-004: A rendered draft parses back into the same 11 sections (the format is self-consistent).

    Given: a generated draft.
    When: its text is parsed with parse_digest().
    Then: the 11 canonical section keys come back in template order with the work date.
    """
    from workdigest.digest.document import parse_digest

    draft, _ = _make(seeded)
    doc = parse_digest(render(draft))
    assert doc.work_date == DAY
    assert [s.key for s in doc.sections] == [s.key for s in draft.sections]


def test_commit_links_prefer_live_branch() -> None:
    """TC-GEN-018: A repo's commit link points at the branch most commits are on, never a backup copy.

    Given: three commits on feat/x and one on backup/x-pre-rebase (processed last).
    When: the Testing/Commits block is built.
    Then: the repo line links feat/x.
    """
    from workdigest.digest.evidence import EvidencePack
    from workdigest.digest.generator import _testing_and_commits

    pack = EvidencePack()
    for index, branch in enumerate(["feat/x", "feat/x", "feat/x", "backup/x-pre-rebase"]):
        pack.add(
            "C",
            kind="commit",
            strength="fact",
            text=f"nav [{branch}] c{index}",
            refs={
                "repo": "nav",
                "branch": branch,
                "sha": f"{index}" * 8,
                "url": f"https://bitbucket.org/ws/nav/commits/branch/{branch}",
            },
        )
    commits = _testing_and_commits(pack)[-1]
    assert commits.children[1].text == "nav (4): https://bitbucket.org/ws/nav/commits/branch/feat/x"


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("Backed the work (C1–C5, T1).", "Backed the work."),
        ("P1 Implement the docs → Done", "Implement the docs → Done"),
        ("A12 / M1: Disagree with done", "Disagree with done"),
        ("Claude Code S1 (09:14–20:58): the move", "Claude Code (09:14–20:58): the move"),
        (
            "billing 714/714 after rebase (T2, C33) backs it",
            "billing 714/714 after rebase backs it",
        ),
        ("The T9 table stays", "The T9 table stays"),
    ],
)
def test_evidence_ids_are_stripped_from_prose(raw: str, clean: str) -> None:
    """TC-GEN-019: Evidence ids the model writes into prose never reach WhatsApp (found in the first live run).

    Given: real leaked forms from the live 23 Oct draft: "(C1–C5, T1)", "P1 …", "A12 / M1:", "S1".
    When: strip_evidence_ids() runs with this draft's known ids.
    Then: the ids are removed and unrelated tokens such as "T9 table" (not an id here) survive.
    """
    from workdigest.digest.generator import strip_evidence_ids

    known = frozenset({"C1", "C5", "T1", "P1", "A12", "M1", "S1", "T2", "C33"})
    assert strip_evidence_ids(raw, known) == clean


def test_manager_never_owner_and_absence_ok(seeded: DigestContext) -> None:
    """TC-GEN-020: A risk owned by the manager is flagged, and "None. No deployment…" is not unverified.

    Given: a risk "Owner: Priya" and releases "None. No deployment to QA or Staging today." citing a session.
    When: the draft is generated.
    Then: the risk is unverified with an owner gap, and releases renders with no [unverified] mark.
    """
    answer = json.loads(json.dumps(GOOD_ANSWER))
    answer["sections"]["risks"] = [
        {"text": "Rollout plan missing. Owner: Priya. Date: 27 Oct.", "evidence_ids": ["S1"]}
    ]
    answer["sections"]["releases"] = [
        {"text": "None. No deployment to QA or Staging today.", "evidence_ids": ["S1"]}
    ]
    draft, _ = _make(seeded, answer)
    text = render(draft)
    assert "owner should be you or a teammate, not Priya" in text
    assert "7. Releases and deployment – None. No deployment to QA or Staging today.\n" in text


def test_prompt_lists_next_working_days(seeded: DigestContext) -> None:
    """TC-GEN-021: The prompt gives the next working days so plan items get real target dates.

    Given: work date Mon 26 Oct 2026.
    When: the draft prompt is built.
    Then: it lists Tue 27 Oct … Mon 02 Nov (skipping the weekend).
    """
    prompt = draft_prompt_text(prepare_inputs(seeded, DAY, llm=None, now=NOW))
    assert "Tue 27 Oct, Wed 28 Oct, Thu 29 Oct, Fri 30 Oct, Mon 02 Nov" in prompt


def test_done_headline_ticket_joins_jira_list(seeded: DigestContext) -> None:
    """TC-GEN-022: A tentative ticket used as a Done-today headline, and named in evidence, is added to the Jira list.

    Given: EIT-9333 mentioned once in a prompt (tentative) and an answer with an "EIT-9333 – …" headline.
    When: the draft is generated.
    Then: the header lists EIT-9333 and there is no "only mentioned in passing" gap for it.
    """
    lines = [
        spool_line(
            claude_payload(
                "UserPromptSubmit", session="S1", prompt="Create EIT-9333 for test hardening"
            ),
            T0 + timedelta(hours=3),
        )
    ]
    with (seeded.paths.spool_dir / "2026-10-26.jsonl").open("a") as handle:
        handle.write("\n".join(lines) + "\n")
    from workdigest.pipeline import build_factory

    ingest_spool(seeded.store, build_factory(seeded.config), seeded.paths.spool_dir, today=DAY)
    answer = json.loads(json.dumps(GOOD_ANSWER))
    answer["sections"]["done_today"].append(
        {"text": "EIT-9333 – Test hardening", "evidence_ids": ["S1"], "children": []}
    )
    draft, _ = _make(seeded, answer)
    assert "EIT-9333" in [line.key for line in draft.header.jira]
    assert not any("EIT-9333 was only mentioned" in g.message for g in draft.gaps)


def test_parallel_summaries_fall_back_per_session(
    store: Store, factory: EventFactory, paths: Paths
) -> None:
    """TC-SUM-003: Summaries run in parallel; one failing session falls back to the deterministic summary.

    Given: three sessions and a fake LLM that fails only for session "bad".
    When: summarize_day() runs with concurrency 3.
    Then: two LLM summaries and one deterministic one are stored, and the failure is logged.
    """
    from workdigest.digest.summarizer import summarize_day

    lines = [
        spool_line(claude_payload("UserPromptSubmit", session=s, prompt=f"work in {s}"), T0)
        for s in ("a", "bad", "c")
    ]
    (paths.spool_dir / "2026-10-26.jsonl").write_text("\n".join(lines) + "\n")
    ingest_spool(store, factory, paths.spool_dir, today=DAY)

    def respond(prompt: Prompt) -> str:
        if "claude:bad" in prompt.user:
            raise LLMError("boom")
        return '{"headline": "ok"}'

    out = summarize_day(
        store,
        DAY.isoformat(),
        redactor=factory.redactor,
        boundary_hour=4,
        llm=FakeProvider(respond),
        concurrency=3,
    )
    methods = {
        r["session_key"]: r["method"]
        for r in store.query("SELECT session_key, method FROM session_summaries")
    }
    assert methods == {"claude:a": "fake", "claude:bad": "deterministic", "claude:c": "fake"}
    assert out["claude:a"].headline == "ok"
    assert store.scalar("SELECT COUNT(*) FROM ingest_errors WHERE stage = 'summarize'") == 1


def test_model_written_day_rating_is_removed(seeded: DigestContext) -> None:
    """TC-GEN-023: A day rating written by the model is removed everywhere; the rating line stays code-owned.

    Given: an answer whose In short starts "Day rating: 3.", a risk child that is only "I'd rate today a 7.",
           and a decision that mentions the rating without a number.
    When: the draft is generated with no rating entered.
    Then: no model-written rating survives, the rest of each statement is kept, the header still asks
          for the rating, and the gap list says what was removed.
    """
    answer = json.loads(json.dumps(GOOD_ANSWER))
    answer["in_short"][0]["text"] = "Day rating: 3. " + answer["in_short"][0]["text"]
    answer["sections"]["risks"][0]["children"] = [
        {"text": "I'd rate today a 7.", "evidence_ids": []}
    ]
    answer["sections"]["decisions"].append(
        {"text": "I added the day-rating line you asked for.", "evidence_ids": []}
    )
    draft, _ = _make(seeded, answer)
    text = render(draft)
    assert "Day rating: 3" not in text
    assert "rate today a 7" not in text
    assert "Billing data loading for EIT-8403 is committed" in text
    assert "I added the day-rating line you asked for." in text
    assert "Day rating: [ask: rate your day 1 / 3 / 7 / 9]" in text
    removed = [g.message for g in draft.gaps if g.message.startswith("removed")]
    assert len(removed) == 2
    assert all("only comes from you" in m for m in removed)


def test_empty_blockers_default_to_none_and_prompt_rules(seeded: DigestContext) -> None:
    """TC-GEN-024: With no blockers the section says "None." with a confirm gap; the prompt forbids ratings and padding blockers.

    Given: an answer with an empty blockers list.
    When: the draft is generated.
    Then: Blockers renders "None.", a blockers confirm gap exists, and the system prompt carries the
          no-rating and real-blockers-only rules.
    """
    answer = json.loads(json.dumps(GOOD_ANSWER))
    answer["sections"]["blockers"] = []
    draft, fake = _make(seeded, answer)
    blockers = draft.section("blockers")
    assert blockers is not None
    assert [s.text for s in blockers.statements] == ["None."]
    assert any(
        g.section == "blockers" and "confirm nothing is blocking" in g.message for g in draft.gaps
    )
    system = next(p.system for p in fake.prompts if "daily status digest" in p.system)
    assert "Never state a day rating" in system
    assert "Missing evidence, unpushed work or not having done something are not blockers" in system


def test_pr_numbers_written_without_hash_are_grounded() -> None:
    """TC-GEN-025: "PRs 3 to 7" and "PR 312" match evidence that writes "#3", "#7" and "#312"; an invented PR number does not.

    Given: PR evidence lines "quiz-service PR #3", "quiz-service PR #7" and "identity-service PR #312".
    When: statements citing them say "PRs 3 to 7" and "PR 312", and another says "PR 313".
    Then: the first is verified; the second is flagged for the number 313.
    """
    evidence = {
        "R1": EvidenceItem(
            id="R1",
            kind="pull_request",
            strength="fact",
            text='quiz-service PR #3 "part 1" OPEN',
        ),
        "R2": EvidenceItem(
            id="R2",
            kind="pull_request",
            strength="fact",
            text='quiz-service PR #7 "part 5" OPEN',
        ),
        "R3": EvidenceItem(
            id="R3",
            kind="pull_request",
            strength="fact",
            text='identity-service PR #312 "logout" OPEN',
        ),
    }
    good = ground_statement(
        Statement(
            text="quiz-service PRs 3 to 7 and identity-service PR 312 are still open.",
            evidence_ids=["R1", "R2", "R3"],
        ),
        "in_short",
        evidence,
    )
    assert good.verified, good.problems
    bad = ground_statement(
        Statement(text="identity-service PR 313 is open.", evidence_ids=["R3"]),
        "in_short",
        evidence,
    )
    assert bad.problems == ["number 313"]
