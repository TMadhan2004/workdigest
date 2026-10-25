"""Learn an engineer's digest format from their past conversation with their manager.

During ``digest setup`` the engineer uploads a ``Me:`` / ``Reply:`` transcript (like
``docs/daily-digest-and-reply.txt``). From it we learn, without an LLM:

* the **template**: section titles, their order and which are one-liners ("7. Releases – None"),
  from the most recent digest; the greeting with the manager's name as a placeholder; the
  header lines the engineer uses (project, team, "In short", day rating); the sign-off;
* the **manager's name**, from the greetings;
* the **rules and asks**, by importing the history (the existing reply parser).

Known sections keep the built-in behaviour (code writes Testing/Commits, PR links,
"None." for releases and blockers…). Unknown sections become custom keys the LLM writes,
guided by an example from the engineer's own digests.

New users get only checks that follow from their own format (all sections present, the header
lines they use); the built-in rule set stays for the original format.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from workdigest.config import Config
from workdigest.digest.document import ParsedDigest, Section, parse_history
from workdigest.digest.repository import DigestRepository
from workdigest.digest.rules import StandingRule
from workdigest.digest.template import DEFAULT_SECTIONS, HEADER_FIELDS, SectionSpec, Template
from workdigest.paths import Paths
from workdigest.store.db import Store

_GREETING = re.compile(
    r"^(?P<word>greetings|good (?:morning|evening)|hi|hello|dear|hey)\s+(?P<name>[A-Z][\w.'-]*)(?P<rest>.*)$",
    re.I,
)
_SIGNOFF = re.compile(r"(?i)^(?:best\s+)?(?:regards|thanks|thank you|cheers|warm regards|best)\b")
_SUFFIX = re.compile(r"\s*\((?:against|day)\b[^)]*\)\s*$", re.I)
_HEADER_NAMES = {
    "project": ("project",),
    "team": ("team members", "team"),
    "in_short": ("in short",),
    "rating": ("day rating", "rating"),
}
# A manager asking for a header line the engineer did not use yet still makes it part of the format.
_ASKED_FOR = {
    "rating": re.compile(r"(?i)\brating\b"),
    "in_short": re.compile(r"(?i)\bin short\b"),
    "team": re.compile(r"(?i)\bteam members\b"),
}
_RULE_FIELDS = {
    "project": "project",
    "team": "team members",
    "in_short": "in short",
    "rating": "day rating",
}
_DEFAULT_GUIDANCE = {s.key: s.guidance for s in DEFAULT_SECTIONS}
MIN_SECTIONS = 2


class OnboardingError(ValueError):
    """The file has no digests we can learn from."""


@dataclass(slots=True)
class Learned:
    """What setup learned from the file, shown to the engineer before it is applied."""

    template: Template
    digests: int
    replies: int
    notes: list[str] = field(default_factory=list)

    def preview(self) -> dict[str, Any]:
        """JSON for the setup wizard's review step."""
        return {
            "manager_name": self.template.manager_name,
            "greeting": self.template.greeting,
            "header_fields": self.template.header_fields,
            "signoff": self.template.signoff,
            "sections": [
                {
                    "key": s.key,
                    "title": s.title,
                    "inline": s.inline,
                    "custom": s.key.startswith("custom_"),
                }
                for s in self.template.sections
            ],
            "digests": self.digests,
            "replies": self.replies,
            "notes": self.notes,
        }


def _title(section: Section) -> str:
    return _SUFFIX.sub("", section.title).strip().rstrip(":")


def _custom_key(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:30] or "section"
    return f"custom_{slug}"


def _example(section: Section) -> str:
    lines = [line.strip() for line in section.lines if line.strip()]
    sample = section.inline or " / ".join(lines[:2])
    return sample[:160]


def _sections(doc: ParsedDigest) -> list[SectionSpec]:
    specs: list[SectionSpec] = []
    used: set[str] = set()
    for section in doc.sections:
        title = _title(section)
        key = section.key
        if key == "other" or key in used:
            key = _custom_key(title)
        while key in used:
            key += "_2"
        used.add(key)
        example = _example(section)
        guidance = _DEFAULT_GUIDANCE.get(key, "")
        if len(example) >= 12:  # "None." teaches nothing
            style = f"Style example from past digests (match the style, never copy its facts): {example}"
            guidance = f"{guidance} {style}".strip()
        specs.append(
            SectionSpec(key=key, title=title, inline=bool(section.inline), guidance=guidance)
        )
    return specs


def _manager(digests: list[ParsedDigest]) -> tuple[str, str]:
    """(manager's name, greeting with a {manager} placeholder), most common first."""
    names: Counter[str] = Counter()
    greetings: Counter[str] = Counter()
    for doc in digests:
        match = _GREETING.match(doc.greeting.strip())
        if match:
            names[match["name"]] += 1
            greetings[f"{match['word']} {{manager}}{match['rest']}"] += 1
    if not names:
        return "", "Greetings {manager},"
    return names.most_common(1)[0][0], greetings.most_common(1)[0][0]


def _header_fields(digests: list[ParsedDigest], replies: list[str]) -> list[str]:
    used = {
        name
        for doc in digests
        for name, aliases in _HEADER_NAMES.items()
        if doc.field_value(*aliases)
    }
    used |= {name for name, ask in _ASKED_FOR.items() if any(ask.search(r) for r in replies)}
    return [name for name in HEADER_FIELDS if name in used]


def _signoff(doc: ParsedDigest) -> str:
    last = doc.closing.strip().split("\n\n")[-1].strip() if doc.closing.strip() else ""
    lines = last.splitlines()
    if lines and len(lines) <= 3 and _SIGNOFF.match(lines[0]) and all(len(x) <= 60 for x in lines):
        return last
    return ""


def learn(text: str) -> Learned:
    """Learn the template from a ``Me:`` / ``Reply:`` transcript."""
    pairs = parse_history(text)
    digests = [p.digest for p in pairs if len(p.digest.sections) >= MIN_SECTIONS]
    if not digests:
        raise OnboardingError(
            "no digests found: each digest starts with a line 'Me:' and has numbered sections "
            "('1. Done today'); each manager reply starts with 'Reply:'"
        )
    replies = [p.reply for p in pairs if p.reply]
    dated = [d for d in digests if d.work_date]
    latest = max(dated, key=lambda d: d.work_date) if dated else digests[-1]  # type: ignore[arg-type, return-value]
    manager, greeting = _manager(digests)
    notes = []
    if not manager:
        notes.append("the manager's name was not found in a greeting; enter it")
    if not replies:
        notes.append("no manager replies found; rules and asks start empty")
    template = Template(
        manager_name=manager,
        greeting=greeting,
        project=latest.field_value("project") or "",
        header_fields=_header_fields(digests, replies),
        sections=_sections(latest),
        signoff=_signoff(latest),
        learned_from=f"{len(digests)} digests, {len(replies)} replies",
    )
    return Learned(template=template, digests=len(digests), replies=len(replies), notes=notes)


def own_rules(template: Template) -> list[StandingRule]:
    """Checks that follow from the engineer's own format."""
    known = [s.key for s in template.sections if not s.key.startswith("custom_")]
    rules = [
        StandingRule(
            rule_id="G01-structure",
            text="Keep your usual sections: " + ", ".join(s.title for s in template.sections) + ".",
            check_kind="section_present",
            check_params={"sections": known},
        )
    ]
    fields = [_RULE_FIELDS[f] for f in template.header_fields]
    if fields:
        rules.append(
            StandingRule(
                rule_id="G02-header",
                text="Include your header lines: " + ", ".join(fields) + ".",
                check_kind="header_present",
                check_params={"fields": fields},
            )
        )
    return rules


def apply(
    paths: Paths, store: Store, config: Config, text: str, learned: Learned
) -> dict[str, int]:
    """Save the template, switch to the engineer's own checks, then import the history."""
    from workdigest.digest.service import import_history  # noqa: PLC0415 - service imports this module's peers

    paths.home.mkdir(parents=True, exist_ok=True)
    (paths.home / "template.json").write_text(
        json.dumps(learned.template.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8"
    )
    DigestRepository(store).use_own_rules(own_rules(learned.template))
    result = import_history(store, config, text)
    return {
        "digests": result.digests,
        "replies": result.replies,
        "asks": result.asks_new,
        "rules": result.rules_new,
    }
