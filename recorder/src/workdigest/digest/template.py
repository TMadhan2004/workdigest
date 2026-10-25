"""The digest template: header fields and the numbered sections, in order.

Every engineer has their own format, so the template is data. ``digest setup`` learns it
from the engineer's past digests (``onboarding.py``) and writes ``<home>/template.json``;
a hand-written ``<home>/template.toml`` also works. Without either, the built-in 11-point
default is used.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from workdigest.models import stable_hash


class SectionSpec(BaseModel):
    """One numbered section of the digest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    title: str
    required: bool = True
    inline: bool = False  # rendered as "N. Title – value" when it is a single line
    guidance: str = ""


# Header lines code can write, in the order they are rendered.
HEADER_FIELDS = ("project", "team", "in_short", "rating")


class Template(BaseModel):
    """Digest layout plus the fixed header text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    manager_name: str = ""
    greeting: str = "Greetings {manager},"
    jira_base_url: str = "https://ei-study.atlassian.net/browse/"
    project: str = ""
    header_fields: list[str] = Field(default_factory=lambda: list(HEADER_FIELDS))
    sections: list[SectionSpec] = Field(default_factory=list)
    signoff: str = ""  # e.g. "Regards,\nArjun", added after the closing
    learned_from: str = ""  # e.g. "4 digests, 3 replies" when learned during setup

    @property
    def version(self) -> str:
        """Short content hash, stored with each generated digest."""
        return stable_hash(self.model_dump(mode="json"))[:12]

    def has_header(self, name: str) -> bool:
        """Whether the header shows ``name`` (one of ``HEADER_FIELDS``)."""
        return name in self.header_fields

    def spec(self, key: str) -> SectionSpec | None:
        """Section spec by key."""
        return next((s for s in self.sections if s.key == key), None)


DEFAULT_SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec(
        key="done_today",
        title="Done today",
        guidance="Per ticket: '<KEY> – <headline>' then dashes with specific outcomes and numbers "
        "from evidence. Then 'Testing' (suite: passed/total, named unit tests added) and "
        "'Commits' (atomic-commit check result, commits per repo with branch links). Past tense.",
    ),
    SectionSpec(
        key="plan_vs_actual",
        title="Plan vs actual",
        guidance="Against the active five-day plan, day N of 5: each planned item → Done / Partly "
        "/ Not started: what happened. Then 'Extra, not in the plan: …'.",
    ),
    SectionSpec(
        key="schedule",
        title="Schedule",
        guidance="On schedule / Behind / Ahead: item, new date if behind.",
    ),
    SectionSpec(key="blockers", title="Blockers", guidance="Items or 'None.'"),
    SectionSpec(
        key="risks",
        title="Risks",
        guidance="Each risk with an owner and a date. Never a bare 'None' after a risky change "
        "(migrations, renames, encryption, data copies, deletes).",
    ),
    SectionSpec(key="decisions", title="Decisions made", guidance="Decision and why."),
    SectionSpec(
        key="releases",
        title="Releases and deployment",
        inline=True,
        guidance="What was deployed where, rollout/rollback location (deployment-instructions.md "
        "when migrations are involved), or 'None.'",
    ),
    SectionSpec(
        key="prs",
        title="PRs and review turnaround",
        guidance="PR link with opened → approved/rejected duration, or the branch list for the "
        "reviewer and why there is no PR.",
    ),
    SectionSpec(key="learnings", title="Learnings", guidance="One-line insights."),
    SectionSpec(
        key="ai_tools",
        title="How I use AI tools and check quality",
        guidance="Which agent/tool did what; then 'Checks:' with test counts, audits, manual checks.",
    ),
    SectionSpec(
        key="plan_upcoming",
        title="Plan for upcoming days",
        guidance="Items with dates and owners where possible.",
    ),
)


def default_template() -> Template:
    """Built-in template matching the 16/17 Oct 11-point structure."""
    return Template(sections=list(DEFAULT_SECTIONS))


def load_template(path: Path) -> Template:
    """The app's template (template.json beside it) if present, else template.toml, else the default."""
    app_template = path.with_name("template.json")
    if app_template.exists():
        try:
            return Template.model_validate(json.loads(app_template.read_text(encoding="utf-8")))
        except ValueError:
            pass  # an invalid app template falls back to the file or the default
    if not path.exists():
        return default_template()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return Template.model_validate(data)
