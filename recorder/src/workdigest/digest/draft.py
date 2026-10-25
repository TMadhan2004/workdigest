"""The draft digest model and its WhatsApp rendering.

WhatsApp formatting rules: plain text only. ``*bold*``, ``_italic_``, numbered lists and
dashes are allowed; ``#`` headings, tables and Markdown links are not. The layout matches
the digests sent so far: section bodies indented by 3 spaces and sub-points by 6.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

GapKind = Literal["missing", "unverified", "rule", "ask", "input", "real_action"]


class EvidenceItem(BaseModel):
    """One piece of captured evidence that statements can cite."""

    model_config = ConfigDict(frozen=True)

    id: str  # C commit, T test, B branch, S session, K ticket, J Jira, R PR, P plan, A ask, M reply
    kind: Literal[
        "commit",
        "test_run",
        "branch",
        "session",
        "ticket",
        "jira",
        "pull_request",
        "plan",
        "ask",
        "reply",
        "repo",
    ]
    strength: Literal["fact", "claim", "context"]
    text: str
    refs: dict[str, str] = Field(default_factory=dict)


class Statement(BaseModel):
    """One line of the digest, with the evidence behind it."""

    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    children: list[Statement] = Field(default_factory=list)
    verified: bool = False
    problems: list[str] = Field(default_factory=list)

    def walk(self) -> list[Statement]:
        """This statement and all its descendants."""
        out = [self]
        for child in self.children:
            out.extend(child.walk())
        return out


class DraftSection(BaseModel):
    """A numbered section."""

    key: str
    number: int
    title: str
    statements: list[Statement] = Field(default_factory=list)
    inline: bool = False
    title_suffix: str = ""  # e.g. "(against the 19 Oct five-day plan, day 4 of 5)"
    trailer: list[str] = Field(default_factory=list)  # e.g. "Extra, not in the plan: …"
    source: Literal["llm", "deterministic", "placeholder", "mixed"] = "llm"


class JiraLine(BaseModel):
    """A ticket in the header."""

    key: str
    title: str | None
    url: str


class Gap(BaseModel):
    """Something to confirm or fix before sending."""

    kind: GapKind
    message: str
    section: str | None = None
    ref: str | None = None


class Header(BaseModel):
    """Everything above section 1."""

    greeting: str
    date_text: str
    jira: list[JiraLine] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    project: str | None = None
    team_members: list[str] = Field(default_factory=list)
    in_short: list[Statement] = Field(default_factory=list)
    rating: int | None = None
    # Which header lines this engineer's format has (see template.HEADER_FIELDS).
    fields: list[str] = Field(default_factory=lambda: ["project", "team", "in_short", "rating"])


class Draft(BaseModel):
    """A complete draft digest."""

    work_date: date
    header: Header
    sections: list[DraftSection]
    closing: list[Statement] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    evidence: dict[str, EvidenceItem] = Field(default_factory=dict)
    provider: str = "none"
    template_version: str = ""
    signoff: str = ""

    def section(self, key: str) -> DraftSection | None:
        """Section by key."""
        return next((s for s in self.sections if s.key == key), None)

    def all_statements(self) -> list[tuple[str, Statement]]:
        """(section key, statement) for every statement, including header and closing."""
        out: list[tuple[str, Statement]] = [("in_short", s) for s in self.header.in_short]
        for section in self.sections:
            for statement in section.statements:
                out.extend((section.key, s) for s in statement.walk())
        out.extend(("closing", s) for s in self.closing)
        return out


# --------------------------------------------------------------------- render
UNVERIFIED_MARK = "[unverified: confirm {what}]"
_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_HEADING = re.compile(r"^\s*#{1,6}\s+", re.M)


def whatsapp_safe(text: str) -> str:
    """Strip Markdown the model may have produced: **bold**, # headings, [text](url) links."""
    text = _MD_LINK.sub(r"\1 \2", text)
    text = _MD_BOLD.sub(r"*\1*", text)
    text = _MD_HEADING.sub("", text)
    return text.replace("|", "/").strip()


def _statement_text(statement: Statement) -> str:
    text = whatsapp_safe(statement.text)
    if not statement.verified and statement.problems:
        what = "; ".join(statement.problems)[:140]
        text = f"{text} {UNVERIFIED_MARK.format(what=what)}"
    return text


def render(draft: Draft) -> str:
    """Render ``draft`` as WhatsApp text."""
    h = draft.header
    out = [h.greeting, "", f"Daily digest – {h.date_text}"]
    if h.jira:
        out.append("Jira:")
        for line in h.jira:
            out.append(f"- {line.key} – {line.title or '[title: confirm]'}")
            out.append(f"  {line.url}")
    out.extend(h.links)
    out.append("")
    fields = set(h.fields)
    about = []
    if "project" in fields:
        about.append(f"Project: {h.project or '[missing: project]'}")
    if "team" in fields:
        team = ", ".join(h.team_members) if h.team_members else "[missing: who you worked with]"
        about.append(f"Team members: {team}")
    summary = []
    if "in_short" in fields:
        in_short = " ".join(_statement_text(s) for s in h.in_short) or "[missing: In short]"
        summary.append(f"In short: {in_short}")
    if "rating" in fields:
        summary.append(
            f"Day rating: {h.rating if h.rating else '[ask: rate your day 1 / 3 / 7 / 9]'}"
        )
    for block in (about, summary):
        if block:
            out.extend([*block, ""])
    for section in draft.sections:
        out.extend(_render_section(section))
        out.append("")
    closing = [whatsapp_safe(s.text) for s in draft.closing if s.text.strip()]
    if closing:
        out.append("\n\n".join(closing))
    if draft.signoff:
        out.extend(["", draft.signoff] if closing else [draft.signoff])
    return "\n".join(out).rstrip() + "\n"


def _render_section(section: DraftSection) -> list[str]:
    title = f"{section.number}. {section.title}"
    if section.title_suffix:
        title += f" {section.title_suffix}"
    if (
        section.inline
        and len(section.statements) <= 1
        and not any(s.children for s in section.statements)
    ):
        value = _statement_text(section.statements[0]) if section.statements else "[missing]"
        return [f"{title} – {value}"]
    lines = [title]
    if not section.statements:
        lines.append("   [missing: no evidence captured for this section]")
    numbered = len(section.statements) > 1 or section.key not in {"blockers", "risks"}
    for index, statement in enumerate(section.statements, start=1):
        prefix = f"   {index}. " if numbered else "   "
        lines.append(prefix + _statement_text(statement))
        for child in statement.children:
            lines.append("      - " + _statement_text(child))
            lines.extend(
                "        - " + _statement_text(grandchild) for grandchild in child.children
            )
    lines.extend(f"   {line}" for line in section.trailer)
    return lines
