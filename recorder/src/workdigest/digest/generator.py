"""Generate the daily digest draft: evidence → LLM → grounding → compliance → WhatsApp text.

The split between code and model is deliberate:

* **Code** writes everything that is pure fact: the header (date, Jira list, project,
  team, rating), the Testing and Commits sub-sections, the PR/branch list, and the
  plan-vs-actual title with "day N of 5".
* **The LLM** writes the narrative ("In short", per-ticket outcomes, plan vs actual
  status, schedule, blockers, risks, decisions, learnings, AI tools, plan, closing). Each
  statement cites evidence ids, and code then grounds every one (see ``grounding.py``).
* With no LLM (``provider = "none"``), the factual parts are still produced and the
  narrative sections are left as visible placeholders. Nothing is invented.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from pydantic import BaseModel, Field

from workdigest.digest.compliance import compliance_gaps
from workdigest.digest.draft import Draft, DraftSection, Gap, Header, JiraLine, Statement, render
from workdigest.digest.evidence import EvidencePack, jira_url
from workdigest.digest.grounding import ground_draft
from workdigest.digest.plan import ActivePlan
from workdigest.digest.repository import Ask
from workdigest.digest.rules import StandingRule
from workdigest.digest.template import SectionSpec, Template
from workdigest.llm.base import LLMError, LLMProvider, Prompt
from workdigest.report.today import is_backup_branch

# Sections code writes entirely; every other section in the template is written by the LLM,
# including custom ones learned from the engineer's own format.
CODE_SECTIONS = frozenset({"prs"})


def llm_sections(template: Template) -> tuple[str, ...]:
    """Keys the LLM writes for this template, in order."""
    return tuple(s.key for s in template.sections if s.key not in CODE_SECTIONS)


class GenStatement(BaseModel):
    """Statement as returned by the model."""

    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    children: list[GenStatement] = Field(default_factory=list)

    def to_statement(self, known_ids: frozenset[str] = frozenset()) -> Statement:
        """Convert to the draft model, removing any evidence ids the model wrote into the text."""
        return Statement(
            text=strip_evidence_ids(self.text.strip(), known_ids),
            evidence_ids=[i.strip() for i in self.evidence_ids],
            children=[c.to_statement(known_ids) for c in self.children],
        )


_ID = r"[ABCJKMPRST]\d{1,3}"
_ID_LIST = rf"{_ID}(?:\s*[–-]\s*{_ID}|\s*[–-]\s*\d{{1,3}})?(?:\s*[,/]\s*{_ID}(?:\s*[–-]\s*{_ID})?)*"
_ID_PAREN = re.compile(rf"\s*\((?:{_ID_LIST})\)")
_ID_PREFIX = re.compile(rf"^(?:{_ID_LIST})\s*[:.]?\s+")
_ID_TOKEN = re.compile(rf"\b{_ID}\b")


def strip_evidence_ids(text: str, known_ids: frozenset[str]) -> str:
    """Remove evidence ids the model put into prose: "(C1–C5, T1)", a leading "A12 / M1:", "P1 Implement…".

    A bare token is only removed when it is a real evidence id of this draft, so text such as
    "T3 table" survives unless T3 exists.
    """
    cleaned = _ID_PAREN.sub("", text)
    cleaned = _ID_PREFIX.sub("", cleaned)
    if known_ids:
        cleaned = _ID_TOKEN.sub(lambda m: "" if m.group(0) in known_ids else m.group(0), cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).replace(" ,", ",").strip()


class GeneratedContent(BaseModel):
    """Full-draft answer schema."""

    in_short: list[GenStatement] = Field(default_factory=list)
    sections: dict[str, list[GenStatement]] = Field(default_factory=dict)
    extra_not_in_plan: str | None = None
    closing: list[GenStatement] = Field(default_factory=list)


class GeneratedSection(BaseModel):
    """Single-section answer schema (regenerate one section)."""

    statements: list[GenStatement] = Field(default_factory=list)
    extra_not_in_plan: str | None = None


@dataclass(slots=True)
class DraftInputs:
    """Everything the generator needs for one day."""

    work_date: date
    template: Template
    pack: EvidencePack
    plan: ActivePlan | None
    open_asks: list[Ask]
    rules: list[StandingRule]
    ticket_titles: dict[str, str]
    project: str | None
    team_members: list[str]
    team_source: str | None  # e.g. "carried from 2026-10-23"
    rating: int | None
    previous_in_short: str | None
    send_deadline: time
    now: datetime
    atlassian_synced: bool = False
    # The engineer's own instructions from My rules (best effort; filters are applied in code).
    instructions: str | None = None
    hygiene_gaps: list[Gap] | None = (
        None  # from Jira; None = not synced, keep the generic R11/R12 gaps
    )


_SYSTEM = """You write an engineer's daily status digest for their manager, sent on WhatsApp.
Rules (follow all):
1. Use ONLY the evidence given. Every statement lists the evidence ids it relies on in "evidence_ids".
2. Never invent numbers, test counts, ticket ids, links, names or dates. Copy numbers exactly from evidence.
3. Agent sessions (S ids) are claims. Facts are commits (C), tests (T), branches (B), Jira activity (J) and pull requests (R). Say "done" only when a fact backs it; otherwise describe it as in progress or write what is still to confirm.
4. Plain WhatsApp text: no Markdown headings, no tables, no **bold**, no links in [text](url) form.
5. Past tense for done work. Plain, specific, no fluff. Each done item says what changed and how it was verified.
6. Risks need an owner and a date. Do not write a bare "None" for risks after migrations, renames, encryption, data copies or deletes.
7. Leave a section's list empty when there is no evidence for it; do not pad.
8. Never write evidence ids (C1, T2, S1, P3, A4, M2…) in the text. They go only in "evidence_ids".
9. Owners are "me" (the engineer) unless the evidence names another person. The manager ({manager}) is never an owner.
10. "done_today" lists only tickets with work backed by facts. Do not add "no commits today" lines; mention untouched tickets in "schedule" instead.
11. "in_short": 3 to 6 short sentences, at most 110 words in total. Plain language a manager can read in 20 seconds.
12. Never state a day rating anywhere; the engineer gives it and code writes the rating line.
13. "blockers" lists only things outside the engineer's control that stop planned work (waiting on a person, access, an environment, a decision). Missing evidence, unpushed work or not having done something are not blockers. If there are none, return an empty list.
Return JSON only."""


def _schema_hint(sections: tuple[str, ...]) -> str:
    keys = ", ".join(f'"{key}": [statement, ...]' for key in sections)
    return (
        "JSON shape:\n"
        '{"in_short": [statement, ...], "sections": {' + keys + "}, "
        '"extra_not_in_plan": "text or null"}\n'
        'statement = {"text": "...", "evidence_ids": ["C1", "T2"], "children": [statement, ...]}\n'
        "done_today: one statement per ticket ('<KEY> – <headline>') whose children are specific outcomes. "
        "Do not add Testing or Commits items; code adds them. "
        "plan_vs_actual: one statement per plan item (P ids): '<item> → Done / Partly / Not started: <what happened>'. "
        "in_short: 3–6 sentences in total: what changed, what was tested, what is pushed, what is NOT deployed."
    )


def _guidance(template: Template) -> str:
    return "\n".join(
        f"- {s.key} ({s.title}): {s.guidance}" for s in template.sections if s.guidance
    )


def build_prompt(inputs: DraftInputs) -> Prompt:
    """The exact prompt for a full draft (also shown by ``digest draft --show-input``)."""
    parts = [
        f"Work date: {inputs.work_date:%a, %d %b %Y}",
        f"Project: {inputs.project or 'unknown'}",
    ]
    if inputs.plan:
        parts.append(
            f"Active plan: {inputs.plan.plan_date:%d %b} five-day plan, today is day {inputs.plan.day} of {inputs.plan.horizon}."
        )
    else:
        parts.append("Active plan: none recorded. Leave plan_vs_actual empty.")
    upcoming = _next_working_days(inputs.work_date, 5)
    parts.append(
        "Next working days (use these for plan and risk target dates): "
        + ", ".join(f"{d:%a %d %b}" for d in upcoming)
        + ". Start each plan_upcoming item with its target day, e.g. "
        f"'{upcoming[0]:%a %d %b}: …'. Risk dates are when the risk will be resolved or checked, never today."
    )
    if inputs.previous_in_short:
        parts.append(
            f"Previous digest's 'In short' (context only, do not repeat): {inputs.previous_in_short}"
        )
    if inputs.instructions:
        parts.append(
            "Engineer's own instructions (follow them unless they conflict with rules 1-14):\n"
            + inputs.instructions
        )
    parts.append("Section guidance:\n" + _guidance(inputs.template))
    parts.append("Evidence:\n" + inputs.pack.render())
    parts.append(_schema_hint(llm_sections(inputs.template)))
    system = _SYSTEM.replace("{manager}", inputs.template.manager_name)
    return Prompt(system=system, user="\n\n".join(parts))


# ------------------------------------------------------------------ assembly
def generate_draft(inputs: DraftInputs, llm: LLMProvider | None) -> Draft:
    """Produce a grounded, compliance-checked draft."""
    content = GeneratedContent()
    gaps: list[Gap] = []
    provider = "none"
    if llm is not None:
        try:
            content = llm.complete_json(build_prompt(inputs), GeneratedContent)
            provider = llm.name
        except LLMError as exc:
            gaps.append(
                Gap(
                    kind="input",
                    message=f"LLM unavailable ({exc}); narrative sections left for you to write",
                )
            )
    draft = _assemble(inputs, content, gaps, provider)
    return finalize(draft, inputs)


def finalize(draft: Draft, inputs: DraftInputs) -> Draft:
    """Ground, check compliance and attach all gaps (used after any edit or regeneration)."""
    base_gaps = [g for g in draft.gaps if g.kind in {"input", "missing"}]
    cleaned = strip_rating_claims(draft.model_copy(update={"gaps": base_gaps}))
    grounded = _flag_manager_as_owner(
        ground_draft(cleaned, _extra_allowed(inputs)), inputs.template.manager_name
    )
    text = render(grounded)
    gaps = grounded.gaps + compliance_gaps(
        text,
        grounded,
        rules=inputs.rules,
        open_asks=inputs.open_asks,
        now=inputs.now,
        send_deadline=inputs.send_deadline,
    )
    if inputs.hygiene_gaps is not None:
        generic = {"R11-ticket-hygiene", "R12-one-in-progress"}
        gaps = [g for g in gaps if g.ref not in generic] + inputs.hygiene_gaps
    return grounded.model_copy(update={"gaps": _dedupe(gaps)})


# A sentence that states a day rating ("Day rating: 3.", "I'd rate today a 7"). The rating
# line is written by code from what you entered; the model must never supply one, and the
# number-grounding check cannot catch it (small numbers are often legitimately allowed).
_RATING_WORD = re.compile(r"(?i)\b(?:rating|rated?|rates)\b")
_RATING_VALUE = re.compile(r"(?i)\b(?:10|[1-9]|one|three|seven|nine)\b")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _without_rating(statement: Statement) -> tuple[Statement | None, list[str]]:
    kept: list[str] = []
    removed: list[str] = []
    for sentence in _SENTENCE.split(statement.text.strip()):
        if _RATING_WORD.search(sentence) and _RATING_VALUE.search(sentence):
            removed.append(sentence)
        else:
            kept.append(sentence)
    children: list[Statement] = []
    for child in statement.children:
        cleaned, child_removed = _without_rating(child)
        removed += child_removed
        if cleaned is not None:
            children.append(cleaned)
    text = " ".join(kept).strip()
    if not text and not children:
        return None, removed
    return statement.model_copy(update={"text": text, "children": children}), removed


def strip_rating_claims(draft: Draft) -> Draft:
    """Remove any day rating the model wrote, and say so in the gap list."""
    removed: list[tuple[str, str]] = []

    def clean(key: str, statements: list[Statement]) -> list[Statement]:
        out: list[Statement] = []
        for statement in statements:
            cleaned, gone = _without_rating(statement)
            removed.extend((key, sentence) for sentence in gone)
            if cleaned is not None:
                out.append(cleaned)
        return out

    header = draft.header.model_copy(update={"in_short": clean("in_short", draft.header.in_short)})
    sections = [
        s.model_copy(update={"statements": clean(s.key, s.statements)}) for s in draft.sections
    ]
    closing = clean("closing", draft.closing)
    gaps = list(draft.gaps) + [
        Gap(
            kind="input",
            section=key,
            message=f'removed "{sentence[:60]}": the day rating only comes from you',
        )
        for key, sentence in removed
    ]
    return draft.model_copy(
        update={"header": header, "sections": sections, "closing": closing, "gaps": gaps}
    )


def _next_working_days(day: date, count: int) -> list[date]:
    out: list[date] = []
    current = day
    while len(out) < count:
        current += timedelta(days=1)
        if current.weekday() < 5:
            out.append(current)
    return out


def _flag_manager_as_owner(draft: Draft, manager: str) -> Draft:
    """The manager is never the owner of the engineer's risks or plan items."""
    pattern = re.compile(rf"(?i)\bowner:?\s*{re.escape(manager)}\b|[–-]\s*{re.escape(manager)}\s*:")
    gaps = list(draft.gaps)
    for key in ("risks", "plan_upcoming"):
        section = draft.section(key)
        for statement in section.statements if section else []:
            if pattern.search(statement.text):
                problem = f"owner should be you or a teammate, not {manager}"
                statement.verified = False
                statement.problems.append(problem)
                gaps.append(
                    Gap(
                        kind="unverified", section=key, message=f"{statement.text[:90]} → {problem}"
                    )
                )
    return draft.model_copy(update={"gaps": gaps})


def _extra_allowed(inputs: DraftInputs) -> set[str]:
    allowed = {str(inputs.work_date.day), str(inputs.work_date.year)}
    if inputs.plan:
        allowed |= {str(inputs.plan.day), str(inputs.plan.horizon), str(inputs.plan.plan_date.day)}
        allowed |= {str(i) for i in range(1, len(inputs.plan.items) + 1)}
    return allowed


def _dedupe(gaps: list[Gap]) -> list[Gap]:
    seen: set[tuple[str, str]] = set()
    out = []
    for gap in gaps:
        key = (gap.kind, gap.message)
        if key not in seen:
            seen.add(key)
            out.append(gap)
    return out


def _header(inputs: DraftInputs, content: GeneratedContent) -> tuple[Header, list[Gap]]:
    """Header lines (all from code) and the gaps they leave."""
    gaps: list[Gap] = []
    template = inputs.template
    pack = inputs.pack
    header = Header(
        # "Greetings Priya,"; without a manager name just "Greetings,".
        greeting=template.greeting.format(manager=template.manager_name).replace(" ,", ","),
        date_text=f"{inputs.work_date:%a, %d %b %Y}",
        jira=[
            JiraLine(
                key=key,
                title=inputs.ticket_titles.get(key),
                url=jira_url(template.jira_base_url, key),
            )
            for key in pack.tickets
        ],
        project=inputs.project,
        team_members=inputs.team_members,
        in_short=[s.to_statement(frozenset(pack.items)) for s in content.in_short],
        rating=inputs.rating,
        fields=list(template.header_fields),
    )
    for line in header.jira:
        if line.title is None:
            gaps.append(
                Gap(
                    kind="input",
                    section="header",
                    message=f"title for {line.key} unknown; confirm it (or sync Jira with `digest atlassian sync`)",
                )
            )
    for key in pack.tentative_tickets:
        gaps.append(
            Gap(
                kind="input",
                section="header",
                message=f"{key} was only mentioned in passing; add it to the Jira list if you worked on it",
            )
        )
    if template.has_header("project") and not inputs.project:
        gaps.append(
            Gap(
                kind="input",
                section="header",
                message="project name missing (set it with `digest close --project`)",
            )
        )
    if not template.has_header("team"):
        pass
    elif inputs.team_source:
        gaps.append(
            Gap(
                kind="input",
                section="header",
                message=f"team members {inputs.team_source}; confirm who you worked with today",
            )
        )
    elif not inputs.team_members:
        gaps.append(
            Gap(
                kind="input",
                section="header",
                message="team members missing (set with `digest close --team`)",
            )
        )
    if template.has_header("rating") and inputs.rating is None:
        gaps.append(
            Gap(
                kind="input",
                section="header",
                message="day rating not given; it is never guessed (`digest close --rating 1|3|7|9`)",
            )
        )

    return header, gaps


# Sections where "nothing" is a normal day: say "None." and ask to confirm instead of a placeholder.
_NONE_BY_DEFAULT = {
    "releases": "no deployment captured; confirm nothing was deployed today",
    "blockers": "no blockers captured; confirm nothing is blocking you",
}


def _section(
    number: int, spec: SectionSpec, inputs: DraftInputs, content: GeneratedContent
) -> tuple[DraftSection, list[Gap]]:
    """One numbered section: LLM statements plus whatever code adds for that section."""
    gaps: list[Gap] = []
    pack = inputs.pack
    statements = [s.to_statement(frozenset(pack.items)) for s in content.sections.get(spec.key, [])]
    section = DraftSection(
        key=spec.key,
        number=number,
        title=spec.title,
        inline=spec.inline,
        statements=statements,
        source="llm",
    )
    if spec.key == "done_today":
        section.statements = statements + _testing_and_commits(pack)
        section.source = "mixed"
    elif spec.key == "plan_vs_actual" and inputs.plan:
        section.title_suffix = f"(against the {inputs.plan.plan_date:%d %b} five-day plan, day {inputs.plan.day} of {inputs.plan.horizon})"
        if content.extra_not_in_plan:
            section.trailer = [f"Extra, not in the plan: {content.extra_not_in_plan.strip()}"]
    elif spec.key == "prs":
        prs = _pr_lines(pack)
        pr_branches = {item.refs.get("branch") for item in pack.of_kind("pull_request")}
        excluded = pr_branches | set(pack.excluded_branches)  # branches of PRs My rules hide
        section.statements = prs + _branch_list(pack, exclude=excluded, with_prs=bool(prs))
        section.source = "deterministic"
        if not inputs.atlassian_synced:
            gaps.append(
                Gap(
                    kind="input",
                    section="prs",
                    message="PR links and turnaround need the Bitbucket token (`digest secrets set bitbucket`); add them or say why there is no PR",
                )
            )
        elif not prs:
            gaps.append(
                Gap(
                    kind="input",
                    section="prs",
                    message="no PRs of yours were found; say why the branches have no PR",
                )
            )
    elif spec.key in _NONE_BY_DEFAULT and not statements:
        section.statements = [Statement(text="None.", verified=True)]
        section.source = "deterministic"
        gaps.append(Gap(kind="input", section=spec.key, message=_NONE_BY_DEFAULT[spec.key]))
    if not section.statements:
        section.source = "placeholder"
        gaps.append(
            Gap(
                kind="missing",
                section=spec.key,
                message=f"'{spec.title}' has no content; write it or confirm 'None.'",
            )
        )
    return section, gaps


def _promote_done_tickets(inputs: DraftInputs, content: GeneratedContent) -> DraftInputs:
    """A ticket the LLM made a Done-today headline joins the Jira list, if evidence mentions it.

    Tickets mentioned only once in prompts start as "tentative". When one is a Done-today
    headline that also appears in evidence (a session or commit names it), it is listed in the
    header. Grounding still checks the headline itself.
    """
    pack = inputs.pack
    corpus = " ".join(item.text for item in pack.items.values())
    promoted: list[str] = []
    for statement in content.sections.get("done_today", []):
        match = re.match(r"^\s*([A-Z][A-Z0-9]+-\d+)\b", statement.text)
        key = match.group(1) if match else None
        if key and key in pack.tentative_tickets and key in corpus:
            promoted.append(key)
    if not promoted:
        return inputs
    pack.tickets.extend(k for k in promoted if k not in pack.tickets)
    pack.tentative_tickets[:] = [k for k in pack.tentative_tickets if k not in promoted]
    return inputs


def _assemble(
    inputs: DraftInputs, content: GeneratedContent, gaps: list[Gap], provider: str
) -> Draft:
    inputs = _promote_done_tickets(inputs, content)
    header, header_gaps = _header(inputs, content)
    gaps.extend(header_gaps)
    sections: list[DraftSection] = []
    for number, spec in enumerate(inputs.template.sections, start=1):
        section, section_gaps = _section(number, spec, inputs, content)
        sections.append(section)
        gaps.extend(section_gaps)
    return Draft(
        work_date=inputs.work_date,
        header=header,
        sections=sections,
        closing=closing_statements(inputs.pack),
        gaps=gaps,
        evidence=dict(inputs.pack.items),
        provider=provider,
        template_version=inputs.template.version,
        signoff=inputs.template.signoff,
    )


def closing_statements(pack: EvidencePack) -> list[Statement]:
    """The closing, written by code: each ask answered since the last digest, and nothing else.

    "<ask>: done – <evidence>." or "<ask>: I disagree – <reason>." Pending asks never appear.
    """
    lines = []
    for item in pack.of_kind("ask"):
        ask, _, answer = item.text.partition(" → ")
        ask = ask.rstrip(" .?!")
        if item.refs.get("status") == "done":
            text = f"{ask}: done – {answer.removeprefix('done; evidence: ')}"
        else:
            text = f"{ask}: I disagree – {answer.removeprefix('I disagree; reason: ')}"
        lines.append(
            Statement(
                text=text if text.endswith((".", "!", "?")) else text + ".", evidence_ids=[item.id]
            )
        )
    return lines


def _testing_and_commits(pack: EvidencePack) -> list[Statement]:
    out: list[Statement] = []
    tests = pack.of_kind("test_run")
    if tests:
        out.append(
            Statement(
                text="Testing",
                children=[
                    Statement(text=_test_line(item.text), evidence_ids=[item.id]) for item in tests
                ],
            )
        )
    commits = pack.of_kind("commit")
    if commits:
        by_repo: dict[str, list[str]] = defaultdict(list)
        branch_votes: dict[str, Counter[str]] = defaultdict(Counter)
        branch_links: dict[tuple[str, str], str] = {}
        for item in commits:
            repo = item.refs.get("repo", "?")
            by_repo[repo].append(item.id)
            branch = item.refs.get("branch", "")
            if branch and item.refs.get("url"):
                branch_votes[repo][branch] += 1
                branch_links[(repo, branch)] = item.refs["url"]
        links: dict[str, str] = {}
        for repo, votes in branch_votes.items():
            # The branch most of today's commits are on; backup copies only as a last resort.
            ranked = sorted(votes.items(), key=lambda kv: (is_backup_branch(kv[0]), -kv[1], kv[0]))
            links[repo] = branch_links[(repo, ranked[0][0])]
        children = [
            Statement(
                text=f"{repo} ({len(ids)}): {links.get(repo, '[branch link: confirm]')}",
                evidence_ids=ids,
            )
            for repo, ids in sorted(by_repo.items())
        ]
        children.insert(
            0,
            Statement(
                text="Atomic-commit check: [missing: result of the atomic-commit check skill]"
            ),
        )
        out.append(Statement(text="Commits", children=children))
    return out


_TEST_LINE = re.compile(
    r"^(?P<suite>.+?): (?P<passed>\d+)/(?P<total>\d+) (?P<unit>\w+) passed(?P<rest>[^(]*)"
)


def _test_line(text: str) -> str:
    match = _TEST_LINE.match(text)
    if not match:
        return text
    rest = match["rest"].strip(" ,")
    return f"{match['suite']}: {match['passed']}/{match['total']} {match['unit']} pass" + (
        f" ({rest})" if rest else ""
    )


def _pr_lines(pack: EvidencePack) -> list[Statement]:
    """One fact line per PR: state and opened → approved/declined turnaround, with the link."""
    out = []
    for item in pack.of_kind("pull_request"):
        url = item.refs.get("url", "")
        out.append(
            Statement(text=f"{item.text}" + (f" – {url}" if url else ""), evidence_ids=[item.id])
        )
    return out


def _branch_list(
    pack: EvidencePack, exclude: set[str | None] | None = None, *, with_prs: bool = False
) -> list[Statement]:
    seen: dict[tuple[str, str], list[str]] = defaultdict(list)
    urls: dict[tuple[str, str], str] = {}
    for item in pack.of_kind("commit"):
        key = (item.refs.get("repo", "?"), item.refs.get("branch", ""))
        if key[1] and not is_backup_branch(key[1]):
            seen[key].append(item.id)
            if item.refs.get("url"):
                urls[key] = item.refs["url"]
    seen = {key: ids for key, ids in seen.items() if key[1] not in (exclude or set())}
    if not seen:
        return []
    children = [
        Statement(
            text=f"{repo} {branch}"
            + (f" – {urls[(repo, branch)]}" if (repo, branch) in urls else ""),
            evidence_ids=ids,
        )
        for (repo, branch), ids in sorted(seen.items())
    ]
    title = (
        "Branches with today's commits and no PR:"
        if with_prs
        else "Branches with today's commits, for review:"
    )
    return [Statement(text=title, children=children)]


# ---------------------------------------------------------- single section
def regenerate_section(draft: Draft, key: str, inputs: DraftInputs, llm: LLMProvider) -> Draft:
    """Rewrite one LLM section and leave every other section untouched."""
    if key == "closing":  # written by code from your answered asks; rebuild it, no LLM needed
        return finalize(
            draft.model_copy(update={"closing": closing_statements(inputs.pack)}), inputs
        )
    if key not in llm_sections(inputs.template) and key != "in_short":
        raise ValueError(f"section {key!r} is written by code, not the LLM; edit it directly")
    base = build_prompt(inputs)
    instruction = (
        f"\n\nRewrite ONLY the '{key}' part. Current draft for context:\n{render(draft)}\n\n"
        'Return JSON only: {"statements": [statement, ...], "extra_not_in_plan": "text or null"}'
    )
    answer = llm.complete_json(
        Prompt(system=base.system, user=base.user + instruction), GeneratedSection
    )
    statements = [s.to_statement(frozenset(draft.evidence)) for s in answer.statements]
    if key == "in_short":
        updated = draft.model_copy(
            update={"header": draft.header.model_copy(update={"in_short": statements})}
        )
    else:
        sections = []
        for section in draft.sections:
            if section.key == key:  # rebuild only the requested section
                kept = (
                    [s for s in section.statements if s.text in {"Testing", "Commits"}]
                    if key == "done_today"
                    else []
                )
                change: dict[str, object] = {"statements": statements + kept, "source": "llm"}
                if key == "plan_vs_actual" and answer.extra_not_in_plan:
                    change["trailer"] = [
                        f"Extra, not in the plan: {answer.extra_not_in_plan.strip()}"
                    ]
                sections.append(section.model_copy(update=change))
                continue
            sections.append(section)
        updated = draft.model_copy(update={"sections": sections})
    return finalize(updated, inputs)
