"""Parse a WhatsApp-formatted daily digest into header fields and numbered sections.

Used for imported history (the previous digest and its five-day plan) and to run the
compliance check on any digest text, including the ones already sent.

The format is the one in the template: a greeting, a ``Daily digest – <date>`` line, a
``Jira:`` list, ``Key: value`` header lines, then top-level ``N. Title`` sections at
column 0 with indented bodies, and an optional closing paragraph. A reply-style preamble
can sit before the date line; the 22 Oct digest has one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

_DATE_LINE = re.compile(r"^\s*Daily digest\s*[–—-]\s*(?P<date>.+?)\s*$", re.I | re.M)
_SECTION = re.compile(r"^(?P<num>\d{1,2})\.\s+(?P<title>\S.*?)\s*$")
_JIRA_ENTRY = re.compile(r"^\s*-\s*(?P<key>[A-Z][A-Z0-9]+-\d+)\s*[–—-]\s*(?P<title>.+?)\s*$")
_URL = re.compile(r"https?://\S+")
_HEADER_FIELD = re.compile(r"^(?P<name>[A-Z][A-Za-z ]{1,30}?):\s*(?P<value>.*)$")
_INLINE_VALUE = re.compile(r"^(?P<title>.+?)\s+[–—-]\s+(?P<value>.+)$")

# Canonical section keys, matched on lower-cased titles (first match wins).
SECTION_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("done_today", ("done today",)),
    ("plan_vs_actual", ("plan vs actual", "plan versus actual")),
    ("schedule", ("schedule",)),
    ("blockers", ("blocker",)),
    ("risks", ("risk",)),
    ("decisions_pending", ("decisions pending", "pending decision")),
    ("decisions", ("decision",)),
    ("releases", ("release", "deployment")),
    ("prs", ("pr", "pull request", "review turnaround")),
    ("learnings", ("learning",)),
    ("ai_tools", ("ai tool", "use ai")),
    ("quality", ("check quality", "quality")),
    (
        "plan_upcoming",
        ("plan for", "upcoming", "next 5 days", "next five days", "tomorrow", "next step"),
    ),
)


def section_key(title: str) -> str:
    """Map a section title to its canonical key (``other`` when unknown).

    Multi-word aliases match as substrings; single words match a whole word or its plural.
    """
    lowered = title.lower()
    words = set(re.findall(r"[a-z]+", lowered))
    for key, aliases in SECTION_ALIASES:
        for alias in aliases:
            if " " in alias:
                if alias in lowered:
                    return key
            elif alias in words or f"{alias}s" in words:
                return key
    return "other"


@dataclass(slots=True)
class JiraEntry:
    """One ticket in the header list."""

    key: str
    title: str
    url: str | None = None


@dataclass(slots=True)
class Section:
    """A top-level numbered section."""

    number: int
    title: str
    key: str
    inline: str = ""  # text after "Title – " on the same line
    lines: list[str] = field(default_factory=list)  # body lines, indentation kept

    @property
    def body(self) -> str:
        """Body text including any inline value."""
        parts = [self.inline] if self.inline else []
        parts += self.lines
        return "\n".join(parts).strip()

    @property
    def is_bare_none(self) -> bool:
        """True when the section only says "None"."""
        return self.body.strip().rstrip(".").strip().lower() in {"none", "nil", "n/a", "-"}


@dataclass(slots=True)
class ParsedDigest:
    """Structured view of a digest's text."""

    text: str
    greeting: str = ""
    preamble: str = ""
    date_text: str = ""
    work_date: date | None = None
    jira: list[JiraEntry] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)  # lower-cased header name -> value
    links: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    closing: str = ""

    def section(self, key: str) -> Section | None:
        """First section with canonical ``key``."""
        return next((s for s in self.sections if s.key == key), None)

    def field_value(self, *names: str) -> str | None:
        """First non-empty header field among ``names`` (lower-case)."""
        for name in names:
            value = self.fields.get(name)
            if value:
                return value
        return None


def parse_digest_date(text: str) -> date | None:
    """Parse ``Mon, 19 Oct 2026`` style dates (weekday optional)."""
    cleaned = re.sub(r"^[A-Za-z]+,\s*", "", text.strip())
    for fmt in ("%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt).date()  # noqa: DTZ007 - date only
        except ValueError:
            continue
    return None


def parse_digest(text: str) -> ParsedDigest:
    """Parse a digest. Unknown parts are preserved, never dropped."""
    doc = ParsedDigest(text=text)
    lines = text.replace("\r\n", "\n").split("\n")
    date_match = _DATE_LINE.search(text)
    date_index = None
    if date_match:
        doc.date_text = date_match["date"]
        doc.work_date = parse_digest_date(date_match["date"])
        date_index = text[: date_match.start()].count("\n")

    first_section = next(
        (
            i
            for i, line in enumerate(lines)
            if _SECTION.match(line) and (date_index is None or i > date_index)
        ),
        len(lines),
    )
    head = lines[: date_index if date_index is not None else 0]
    _parse_preamble(doc, head)
    header_lines = lines[(date_index + 1) if date_index is not None else 0 : first_section]
    _parse_header(doc, header_lines)
    _parse_sections(doc, lines[first_section:])
    return doc


def _parse_preamble(doc: ParsedDigest, head: list[str]) -> None:
    blocks = _blocks(head)
    if blocks and re.match(r"^(greetings|hi|hello|dear)\b", blocks[0][0].strip(), re.I):
        doc.greeting = blocks[0][0].strip()
        rest = blocks[0][1:]
        blocks = ([rest] if rest else []) + blocks[1:]
    doc.preamble = "\n\n".join("\n".join(b).strip() for b in blocks).strip()


def _parse_header(doc: ParsedDigest, header_lines: list[str]) -> None:
    current_field: str | None = None
    in_jira = False
    for raw in header_lines:
        line = raw.rstrip()
        if not line.strip():
            current_field = None
            continue
        if line.strip().lower() == "jira:":
            in_jira = True
            current_field = None
            continue
        entry = _JIRA_ENTRY.match(line)
        if in_jira and entry:
            doc.jira.append(JiraEntry(entry["key"], entry["title"]))
            continue
        url = _URL.search(line)
        if (
            in_jira
            and url
            and line.strip() == url.group(0)
            and doc.jira
            and doc.jira[-1].url is None
        ):
            doc.jira[-1].url = url.group(0)
            continue
        field_match = _HEADER_FIELD.match(line.strip())
        if field_match:
            in_jira = False
            name = field_match["name"].strip().lower()
            value = field_match["value"].strip()
            if _URL.fullmatch(value):
                doc.links.append(value)
            doc.fields[name] = value
            current_field = name
            continue
        if current_field:
            doc.fields[current_field] = (doc.fields[current_field] + " " + line.strip()).strip()
        elif url:
            doc.links.append(url.group(0))


def _parse_sections(doc: ParsedDigest, lines: list[str]) -> None:
    current: Section | None = None
    closing: list[str] = []
    blank_before = False
    for raw in lines:
        line = raw.rstrip()
        match = _SECTION.match(line)
        if match:
            title = match["title"]
            inline = ""
            inline_match = _INLINE_VALUE.match(title)
            if inline_match and section_key(inline_match["title"]) != "other":
                title, inline = inline_match["title"], inline_match["value"]
            current = Section(int(match["num"]), title.strip(), section_key(title), inline.strip())
            doc.sections.append(current)
            closing = []
            blank_before = False
            continue
        if not line.strip():
            blank_before = True
            if closing:
                closing.append("")
            continue
        unindented = not line.startswith((" ", "\t"))
        if closing or (current is not None and unindented and blank_before and current.lines):
            closing.append(line)
        elif current is not None:
            current.lines.append(line)
        else:
            closing.append(line)
        blank_before = False
    doc.closing = "\n".join(closing).strip()


def _blocks(lines: list[str]) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


# --------------------------------------------------------------- history files
_ROLE_MARKER = re.compile(r"^(?P<role>Me|Reply):\s*$", re.M)


@dataclass(slots=True)
class HistoryPair:
    """One sent digest and the manager's reply to it."""

    digest: ParsedDigest
    reply: str | None


def parse_history(text: str) -> list[HistoryPair]:
    """Split a ``Me:`` / ``Reply:`` transcript (like docs/daily-digest-and-reply.txt)."""
    markers = list(_ROLE_MARKER.finditer(text))
    pairs: list[HistoryPair] = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        body = text[marker.end() : end].strip()
        if marker["role"] == "Me":
            pairs.append(HistoryPair(parse_digest(body), None))
        elif pairs and pairs[-1].reply is None:
            pairs[-1].reply = body
    return pairs
