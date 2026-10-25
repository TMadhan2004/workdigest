"""Grounding: check that each statement is backed by the evidence it cites.

This runs in code after generation; the prompt asking the model to behave is not enough.

* Every number, ticket key, URL and commit SHA in a statement must appear in the cited
  evidence. Derived counts are allowed when they equal the number of cited items of one
  kind ("3 commits" citing 3 commit items).
* In "done" sections (done today, releases, PRs), a statement backed only by session
  claims (what an agent said it did) is not verified: it needs a commit, a PR or a
  passing test.
* Citations to unknown evidence ids are dropped and reported.

A statement that fails keeps its text, gets ``verified=False`` with its problems, and is
rendered with ``[unverified: confirm …]`` and listed in the gap panel. It is never
silently removed.
"""

from __future__ import annotations

import re
from collections import Counter

from workdigest.digest.draft import Draft, EvidenceItem, Gap, Statement

FACT_REQUIRED_SECTIONS = {"done_today", "releases", "prs"}
NO_EVIDENCE_OK = {
    "blockers",
    "risks",
    "decisions",
    "learnings",
    "plan_upcoming",
    "closing",
    "schedule",
}
# Standalone numbers only: skip identifiers such as v4, TC-UN-9701, 00008.sql or 35.2s.
_NUMBER = re.compile(r"(?<![\w.#/:-])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\w-]|\.\d|\.[A-Za-z])")
_RATIO = re.compile(r"\b(\d[\d,]*)\s*/\s*(\d[\d,]*)\b")
_REFERENCE = re.compile(r"#(\d{1,7})\b")
_TICKET = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{1,7}\b")
_URL = re.compile(r"https?://[^\s)>\]]+")
_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
_ALWAYS_OK = {"0", "1"}
# "None." / "None. No deployment to QA or Staging today." state an absence, not a done fact.
_ABSENCE = re.compile(r"(?i)^(?:none|nothing|n/a|no (?:blockers|releases|deployments?))\b")
PLACEHOLDER = re.compile(r"\[(?:missing|confirm|ask|title: confirm|branch link: confirm)[^\]]*\]")
_WORD_NUMBERS = {
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def _norm(number: str) -> str:
    return number.replace(",", "")


# Dates and times are commitments or context ("owner: me, by 28 Oct"), not measured facts.
_DATE_EXPR = re.compile(
    r"(?i)\b\d{1,2}[ -](?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*(?:[ -]\d{4})?\b|"
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* \d{1,2}(?:, \d{4})?\b|"
    r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}(?::\d{2})?\s?(?:am|pm)\b|\b\d{1,2}:\d{2}\b|\bday \d+ of \d+\b"
)


def _numbers(text: str) -> set[str]:
    without_urls = _DATE_EXPR.sub(" ", _URL.sub(" ", text))
    without_keys = _TICKET.sub(" ", without_urls)
    found = {_norm(n) for n in _NUMBER.findall(without_keys)}
    for left, right in _RATIO.findall(without_keys):
        found |= {_norm(left), _norm(right)}
    return found


def ground_statement(
    statement: Statement,
    section_key: str,
    evidence: dict[str, EvidenceItem],
    extra_allowed: set[str] | None = None,
) -> Statement:
    """Return a copy of ``statement`` with ``verified`` and ``problems`` set."""
    problems: list[str] = []
    cited = [evidence[i] for i in statement.evidence_ids if i in evidence]
    unknown = [i for i in statement.evidence_ids if i not in evidence]
    if unknown:
        problems.append(f"unknown evidence {', '.join(unknown)}")
    corpus = " ".join(item.text + " " + " ".join(item.refs.values()) for item in cited)
    allowed_numbers = _numbers(corpus) | _ALWAYS_OK | (extra_allowed or set())
    # Evidence writes references as "#312" (skipped by _NUMBER); prose says "PR 312" or "PRs 3 to 7".
    allowed_numbers |= set(_REFERENCE.findall(corpus))
    allowed_numbers |= {str(n) for n in Counter(item.kind for item in cited).values()}
    allowed_numbers.add(str(len(cited)))

    text = statement.text
    missing_numbers = sorted(_numbers(text) - allowed_numbers, key=lambda n: (len(n), n))
    if missing_numbers:
        problems.append("number " + ", ".join(missing_numbers))
    for key in sorted(set(_TICKET.findall(text))):
        if key not in corpus:
            problems.append(f"ticket {key}")
    for url in _URL.findall(text):
        if url.rstrip(".,;") not in corpus:
            problems.append("link")
    for sha in _SHA.findall(text):
        if not any(sha in item.refs.get("sha", "") or sha in item.text for item in cited):
            problems.append(f"commit {sha}")

    is_absence = bool(_ABSENCE.match(text.strip()))
    has_content = bool(text.strip()) and not PLACEHOLDER.search(text) and not is_absence
    if not cited and has_content and section_key not in NO_EVIDENCE_OK and not statement.children:
        problems.append("no evidence cited")
    if (
        section_key in FACT_REQUIRED_SECTIONS
        and not is_absence
        and cited
        and all(item.strength != "fact" for item in cited)
        and not statement.children
    ):
        problems.append("only an agent's report backs this; no commit, PR or passing test")

    children = [
        ground_statement(c, section_key, evidence, extra_allowed) for c in statement.children
    ]
    return statement.model_copy(
        update={"verified": not problems, "problems": problems, "children": children}
    )


def ground_draft(draft: Draft, extra_allowed: set[str] | None = None) -> Draft:
    """Ground every statement in ``draft`` and append an ``unverified`` gap for each failure."""
    in_short = [
        ground_statement(s, "in_short", draft.evidence, extra_allowed)
        for s in draft.header.in_short
    ]
    sections = [
        section.model_copy(
            update={
                "statements": [
                    ground_statement(s, section.key, draft.evidence, extra_allowed)
                    for s in section.statements
                ]
            }
        )
        for section in draft.sections
    ]
    closing = [ground_statement(s, "closing", draft.evidence, extra_allowed) for s in draft.closing]
    grounded = draft.model_copy(
        update={
            "header": draft.header.model_copy(update={"in_short": in_short}),
            "sections": sections,
            "closing": closing,
        }
    )
    gaps = list(draft.gaps)
    for section_key, statement in grounded.all_statements():
        if statement.problems:
            gaps.append(
                Gap(
                    kind="unverified",
                    section=section_key,
                    message=f"{statement.text[:90]} → confirm {'; '.join(statement.problems)}",
                )
            )
    return grounded.model_copy(update={"gaps": gaps})
