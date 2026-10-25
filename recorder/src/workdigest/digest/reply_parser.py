"""Turn the manager's free-form WhatsApp reply into typed, reviewable items.

There are two stages, so the result is useful even with no LLM:

1. **Segment** (deterministic): find the greeting and sign-off, headed sections
   ("Bugs", "Tasks", "PRs", "Factory / Tools", "Digest", "Attendance"), bullets, numbered
   points, and prose paragraphs split into sentences.
2. **Classify**: keyword heuristics give every segment a type. An optional LLM pass can
   refine the types and wording. Its answer is validated, and it can only relabel
   segments that exist, never invent new ones.

Item types: ``standing_rule`` | ``one_time_action`` | ``question`` | ``feedback`` |
``info_only``. Every item is shown to the user to confirm before it becomes an ask or a rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from workdigest.ingest.context import TicketMatcher
from workdigest.llm.base import LLMError, LLMProvider, Prompt

ItemType = Literal["standing_rule", "one_time_action", "question", "feedback", "info_only"]
ITEM_TYPES: tuple[str, ...] = (
    "standing_rule",
    "one_time_action",
    "question",
    "feedback",
    "info_only",
)

_GREETING = re.compile(r"^(?:hi|hello|hey|dear|greetings)\b[^\n]{0,40}$", re.I)
_SIGNOFF = re.compile(
    r"^(?:best regards|regards|thanks(?: and regards)?|cheers|warm regards)\b", re.I
)
_BULLET = re.compile(r"^\s*(?:[-•*▪]|\d{1,2}[.)])\s+(?P<text>.+)$")
_HEADING = re.compile(r"^[A-Z][A-Za-z/&' ]{1,30}$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")
_KNOWN_HEADINGS = {
    "bugs",
    "tasks",
    "prs",
    "factory",
    "factory / tools",
    "tools",
    "digest",
    "attendance",
    "jira",
    "questions",
}

_STANDING = re.compile(
    r"(?i)\b(?:from (?:the )?next digest|from now on|from tomorrow|every (?:day|digest|pr|time)|each (?:day|digest|diff|pr|time)|"
    r"before every|always|keep (?:naming|only|working)|with no exception|no exception|please follow|"
    r"going forward|in (?:each|every) digest|would help me too|send before|"
    r"one (?:ticket )?in progress at a time|on office days|on wfh days|if you are away)\b"
)
_ACTION_VERBS = (
    r"(?:raise|log|add|move|open|run|fill|share|agree|check|recheck|turn|load|use|create|write|"
    r"update|fix|tell|keep|put|link|close|review|split|rename|demo|schedule|send|set|mark|document|"
    r"plan|try|work|build|join|start|ask)"
)
# An imperative verb at the start of a clause: sentence start, after "please"/"for tomorrow",
# or after ":", ";", "," or "..." ("once X reviews the guide, turn it into a skill").
_ACTION = re.compile(
    r"(?i)(?:^|[:;,]\s+|\.\.\.\s*|\b(?:please|kindly|for tomorrow,?|can you|could you|then|and)\s+)"
    r"(?:also\s+|still\s+)?" + _ACTION_VERBS + r"\b"
    r"|\bI(?:'d| would) (?:still )?like\b"
    r"|^(?:logging|moving|raising|adding|opening|filling|sharing)\b"
)
_PRAISE = re.compile(
    r"(?i)\b(?:very good|good (?:work|discipline|engineering)|excellent|strong work|great|well done|"
    r"thank(?:s| you)|appreciate|nice|important|reads much easier|is the .* i hope for|was right)\b"
)
_ATTENDANCE = re.compile(
    r"(?i)\b(?:punch(?:ed)? in|arriv|average|9(?::00)?\s?am|8:59|office days?|wfh|scrum)\b"
)
_REAL_ACTION = re.compile(
    r"(?i)\b(?:raise|log|move|open|run|fill|share|agree|create|link|close|demo|add (?:a |an )?(?:short )?"
    r"(?:comment|activity|contract test|test)|recheck|split|load)\b"
)
_DUE = re.compile(
    r"(?i)\b(?:due|by|before)\s+(?P<date>\d{1,2}[- ](?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[- ]\d{4}|"
    r"tomorrow|today|eod|end of day)"
)


@dataclass(slots=True)
class Segment:
    """A unit of the reply that can become one item."""

    idx: int
    text: str
    heading: str | None = None
    kind: Literal["bullet", "sentence", "greeting", "signoff"] = "sentence"


class ReplyItem(BaseModel):
    """One extracted item, before the user confirms it."""

    model_config = ConfigDict(extra="forbid")

    idx: int
    type: ItemType
    text: str
    heading: str | None = None
    related_tickets: list[str] = Field(default_factory=list)
    due_date: str | None = None
    needs_real_action: bool = False
    method: Literal["heuristic", "llm", "manual"] = "heuristic"


@dataclass(slots=True)
class ParsedReply:
    """Result of parsing one reply."""

    greeting: str = ""
    signoff: str = ""
    items: list[ReplyItem] = field(default_factory=list)

    def of_type(self, *types: str) -> list[ReplyItem]:
        """Items whose type is one of ``types``."""
        return [i for i in self.items if i.type in types]


# ----------------------------------------------------------------- segmenting
def segment(text: str) -> tuple[str, str, list[Segment]]:
    """Return (greeting, signoff, segments) for a reply."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    greeting = ""
    signoff_lines: list[str] = []
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and _GREETING.match(lines[0].strip()):
        greeting = lines.pop(0).strip()
    for i, line in enumerate(lines):
        if _SIGNOFF.match(line.strip()):
            signoff_lines = [entry.strip() for entry in lines[i:] if entry.strip()]
            lines = lines[:i]
            break

    segments: list[Segment] = []
    heading: str | None = None
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            for sentence in _sentences(" ".join(p.strip() for p in paragraph)):
                segments.append(Segment(len(segments), sentence, heading, "sentence"))
            paragraph.clear()

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        bullet = _BULLET.match(line)
        next_line = next((entry for entry in lines[i + 1 :] if entry.strip()), "")
        is_heading = (
            not bullet
            and _HEADING.match(stripped)
            and (
                stripped.lower() in _KNOWN_HEADINGS
                or (_BULLET.match(next_line) and len(stripped.split()) <= 3)
            )
        )
        if is_heading:
            flush()
            heading = stripped
            continue
        if bullet:
            flush()
            segments.append(Segment(len(segments), bullet["text"].strip(), heading, "bullet"))
            continue
        if (
            segments
            and segments[-1].kind == "bullet"
            and line.startswith(("  ", "\t"))
            and not paragraph
        ):
            segments[-1].text += " " + stripped  # wrapped bullet continuation
            continue
        if (
            heading
            and not paragraph
            and segments
            and segments[-1].heading == heading
            and segments[-1].kind == "bullet"
        ):
            heading = None  # prose after a headed list leaves that heading
        paragraph.append(stripped)
    flush()
    return greeting, " ".join(signoff_lines), segments


def _sentences(text: str) -> list[str]:
    # Keep "..." pauses ("Very good... this reads") inside one sentence.
    protected = text.replace("...", "…")
    return [p.strip().replace("…", "...") for p in _SENTENCE_END.split(protected) if p.strip()]


# ------------------------------------------------------------- classification
def classify(segment_: Segment) -> ItemType:  # noqa: PLR0911 - an ordered decision table
    """Heuristic type for one segment."""
    text = segment_.text
    heading = (segment_.heading or "").lower()
    if heading == "attendance" or (
        _ATTENDANCE.search(text) and not _ACTION.search(text.split(",")[0])
    ):
        return "info_only"
    if text.rstrip().endswith("?"):
        return "question"
    if heading == "digest" or _STANDING.search(text):
        return "standing_rule"
    if _ACTION.search(text):
        return "one_time_action"
    if heading in {"bugs", "tasks", "prs", "factory", "factory / tools", "tools"}:
        return "one_time_action"
    if _PRAISE.search(text):
        return "feedback"
    return "info_only"


def due_date_of(text: str, reply_date: date | None) -> str | None:
    """Explicit due date in ``text`` (ISO), resolving "tomorrow" against the reply date."""
    match = _DUE.search(text)
    if not match:
        return None
    value = match["date"].lower()
    if value in {"today", "eod", "end of day"} and reply_date:
        return reply_date.isoformat()
    if value == "tomorrow" and reply_date:
        return (reply_date + timedelta(days=1)).isoformat()
    for fmt in ("%d-%b-%Y", "%d %b %Y", "%d-%B-%Y", "%d %B %Y"):
        try:
            return datetime.strptime(match["date"], fmt).date().isoformat()  # noqa: DTZ007
        except ValueError:
            continue
    return None


def parse_reply(
    text: str,
    tickets: TicketMatcher,
    reply_date: date | None = None,
    llm: LLMProvider | None = None,
) -> ParsedReply:
    """Segment and classify a reply; optionally refine types with ``llm``."""
    greeting, signoff, segments = segment(text)
    items = [
        ReplyItem(
            idx=s.idx,
            type=classify(s),
            text=s.text,
            heading=s.heading,
            related_tickets=tickets.find(s.text),
            due_date=due_date_of(s.text, reply_date),
        )
        for s in segments
    ]
    if llm is not None and items:
        items = _refine_with_llm(items, llm)
    for item in items:
        item.needs_real_action = item.type == "one_time_action" and bool(
            _REAL_ACTION.search(item.text)
        )
    return ParsedReply(greeting=greeting, signoff=signoff, items=items)


class _LLMLabel(BaseModel):
    idx: int
    type: ItemType


class _LLMLabels(BaseModel):
    items: list[_LLMLabel]


_SYSTEM = """You label items from a manager's reply to an engineer's daily status digest.
Types:
- standing_rule: a lasting requirement for every future digest or way of working (format, timing, habits).
- one_time_action: a specific thing to do once (raise a bug, move a ticket, add a comment, run a tool).
- question: asks the engineer something and expects an answer.
- feedback: praise or acknowledgement with no request.
- info_only: information such as attendance figures, with nothing to do.
Return JSON only: {"items": [{"idx": <int>, "type": "<type>"}]} with one entry per input item.
Do not add, merge or rewrite items."""


def _refine_with_llm(items: list[ReplyItem], llm: LLMProvider) -> list[ReplyItem]:
    listing = "\n".join(
        f"{i.idx}. [{i.heading or '-'}] (heuristic: {i.type}) {i.text}" for i in items
    )
    try:
        labels = llm.complete_json(Prompt(system=_SYSTEM, user=f"Items:\n{listing}"), _LLMLabels)
    except LLMError:
        return items  # heuristic result stands; the user confirms every item anyway
    by_idx = {label.idx: label.type for label in labels.items}
    refined = []
    for item in items:
        if item.idx in by_idx:
            refined.append(item.model_copy(update={"type": by_idx[item.idx], "method": "llm"}))
        else:
            refined.append(item)
    return refined
