"""Learning a teammate's own digest format from their conversation file, and drafting in it."""

from __future__ import annotations

import json
import re

import pytest

from tests.digest.conftest import history_text
from tests.digest.test_generation import DAY, NOW, seeded  # noqa: F401 - fixture
from workdigest.config import Config
from workdigest.digest import onboarding
from workdigest.digest.draft import render
from workdigest.digest.repository import DigestRepository
from workdigest.digest.service import DigestContext, create_draft
from workdigest.paths import Paths
from workdigest.store.db import Store

# A different teammate: another manager, fewer sections, a custom one, no rating, a sign-off.
TEAMMATE = """Me:
Hi Sana,

Daily digest – Thu, 22 Oct 2026
Jira:
- DEMO-101 – Add invoice export
  https://ei-study.atlassian.net/browse/DEMO-101

Project: Billing
In short: Invoice export works for PDF; CSV is next.

1. Done today
   1. DEMO-101 – PDF export with totals and tax lines.
2. Customer impact
   1. Finance can download invoices without a support ticket.
3. Blockers – None.
4. Tomorrow
   1. Fri 23 Oct: CSV export.

Regards,
Vikram

Reply:
Hi Vikram,

Nice. Please add the team members line too.

Thanks
Sana

Me:
Hi Sana,

Daily digest – Fri, 23 Oct 2026

Project: Billing
Team members: Meera
In short: CSV export is done.

1. Done today
   1. DEMO-101 – CSV export, 12 tests added.
2. Customer impact
   1. Finance can open exports in Excel.
3. Blockers – None.
4. Tomorrow
   1. Mon 26 Oct: export history page.

Regards,
Vikram
"""


def test_learns_a_teammates_format() -> None:
    """TC-ONB-001: The template comes from the teammate's latest digest, greeting and sign-off.

    Given: two digests to "Sana" with four sections, one custom, an inline blocker line, no rating.
    When: the file is learned.
    Then: manager Sana, "Hi {manager},", the four sections in order (Customer impact custom),
          Blockers inline, header project/team/In short (no rating), the sign-off, and no notes.
    """
    learned = onboarding.learn(TEAMMATE)
    template = learned.template
    assert template.manager_name == "Sana"
    assert template.greeting == "Hi {manager},"
    assert [(s.key, s.title) for s in template.sections] == [
        ("done_today", "Done today"),
        ("custom_customer_impact", "Customer impact"),
        ("blockers", "Blockers"),
        ("plan_upcoming", "Tomorrow"),
    ]
    assert template.spec("blockers").inline  # type: ignore[union-attr]
    assert "Finance can open exports" in template.spec("custom_customer_impact").guidance  # type: ignore[union-attr]
    assert template.header_fields == ["project", "team", "in_short"]
    assert template.signoff == "Regards,\nVikram"
    assert template.project == "Billing"
    assert (learned.digests, learned.replies, learned.notes) == (2, 1, [])
    assert learned.preview()["sections"][1]["custom"] is True


def test_learns_the_example_file() -> None:
    """TC-ONB-002: The example conversation gives back the 11-point format, with a rating asked for.

    Given: docs/daily-digest-and-reply.txt (the digests have no rating line; a reply asks for one).
    When: it is learned.
    Then: manager Priya, 11 sections from the latest digest, and all four header lines.
    """
    template = onboarding.learn(history_text()).template
    assert template.manager_name == "Priya"
    assert len(template.sections) == 11
    assert template.sections[1].title == "Plan vs actual"
    assert template.header_fields == ["project", "team", "in_short", "rating"]


def test_file_without_digests_is_explained() -> None:
    """TC-ONB-003: A file with no recognisable digest says what the format should be.

    Given: plain notes without "Me:" markers.
    When: it is learned.
    Then: an OnboardingError that explains "Me:" and "Reply:".
    """
    with pytest.raises(onboarding.OnboardingError, match=r"'Me:'.*'Reply:'"):
        onboarding.learn("some notes\n1. not a digest\n")


def test_apply_and_draft_in_the_teammates_format(
    seeded: DigestContext,  # noqa: F811
    paths: Paths,
    store: Store,
    config: Config,
) -> None:
    """TC-ONB-004: After setup the draft follows the teammate's format and checks.

    Given: the teammate file applied (template.json, own checks, history imported).
    When: a draft is made without an LLM.
    Then: "Hi Sana,", sections 1–4 with "Customer impact", "Blockers – None.", no Day rating
          line and no rating gap, the sign-off at the end; the built-in R-rules are gone and the
          G-rules are in place, and re-seeding does not bring the R-rules back.
    """
    learned = onboarding.learn(TEAMMATE)
    counts = onboarding.apply(paths, store, config, TEAMMATE, learned)
    assert counts["digests"] == 2
    assert json.loads((paths.home / "template.json").read_text())["manager_name"] == "Sana"
    repo = DigestRepository(store)
    repo.seed_rules()
    ids = {r.rule_id for r in repo.rules()}
    assert {"G01-structure", "G02-header"} <= ids
    assert not any(i.startswith("R0") for i in ids)

    cfg = Config.model_validate(
        {
            **config.model_dump(mode="json"),
            "digest": {**config.digest.model_dump(), "manager_name": ""},
        }
    )
    _, draft = create_draft(DigestContext(store, cfg, paths), DAY, llm=None, now=NOW)
    text = render(draft)
    assert text.startswith("Hi Sana,\n")
    titles = [
        m.group(0)
        for line in text.splitlines()
        if (m := re.match(r"^\d\. [A-Z][a-z]+(?: [a-z]+)?", line))
    ]
    assert titles == ["1. Done today", "2. Customer impact", "3. Blockers", "4. Tomorrow"]
    assert "3. Blockers – None." in text
    assert "Day rating" not in text
    assert not any("rating" in g.message for g in draft.gaps)
    assert text.rstrip().endswith("Regards,\nVikram")


def test_team_members_count_as_risk_owners() -> None:
    """TC-ONB-005: Anyone on the digest's own Team members line can own a risk; other names cannot.

    Given: a risk owned by "Sana" with Sana on the team line, and the same risk without her there.
    When: the risk rule is checked.
    Then: no "no owner" problem in the first; a "no owner" problem in the second.
    """
    from workdigest.digest.document import parse_digest
    from workdigest.digest.rules import SEED_RULES, evaluate

    rule = next(r for r in SEED_RULES if r.check_kind == "risk_owner_date")
    body = "Daily digest – Fri, 23 Oct 2026\n\nTeam members: {team}\n\n1. Risks\n   1. Export may time out – Sana, Mon 26 Oct\n"
    assert evaluate(rule, parse_digest(body.format(team="Sana, Meera"))).passed
    result = evaluate(rule, parse_digest(body.format(team="Meera")))
    assert "no owner" in result.message
