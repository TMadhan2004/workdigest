"""My rules: free-text rules the user types in the app, enforced in code where possible.

A rule such as "Don't include quiz-service PRs 3-7" or "Hide PRs open more than 30 days" is
read (by the LLM, or by patterns without one) into :class:`RuleFilters`. The user confirms
what was understood before it takes effect. Filters remove items from the evidence pack, so
an excluded PR, ticket or repo never reaches the prompt, the PR section, the branch list or
the header. Anything that is not a filter becomes an ``instruction`` for the LLM, which is
best effort and labelled as such in the app.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from workdigest.llm.base import LLMError, LLMProvider, Prompt
from workdigest.store.db import Store

_TICKET = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{1,7}\b")
_PR_RANGE = re.compile(
    r"(?i)\b(?:prs?|pull[ -]?requests?)\s*#?(\d{1,6})(?:\s*(?:-|–|to|through|thru)\s*#?(\d{1,6}))?"
)
_PR_HASH = re.compile(r"#(\d{1,6})\b")
_AGE = re.compile(r"(?i)(?:older than|more than|over|longer than)\s+(\d{1,4})\s*(day|week|month)s?")
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30}


class PrRef(BaseModel):
    """Pull requests of one repo (``repo`` None means any repo)."""

    repo: str | None = None
    numbers: list[int] = Field(default_factory=list)


class RuleFilters(BaseModel):
    """What one rule (or all active rules, merged) removes from a digest."""

    exclude_prs: list[PrRef] = Field(default_factory=list)
    exclude_prs_older_than_days: int | None = Field(default=None, ge=1)
    exclude_tickets: list[str] = Field(default_factory=list)
    exclude_repos: list[str] = Field(default_factory=list)
    instruction: str | None = None

    @field_validator("exclude_tickets")
    @classmethod
    def _tickets(cls, value: list[str]) -> list[str]:
        return sorted({v.strip().upper() for v in value if _TICKET.fullmatch(v.strip().upper())})

    def is_empty(self) -> bool:
        """True when the rule removes nothing and adds no instruction."""
        return not (
            self.exclude_prs
            or self.exclude_prs_older_than_days
            or self.exclude_tickets
            or self.exclude_repos
            or self.instruction
        )

    def excludes_repo(self, repo: str | None) -> bool:
        """Whether everything from ``repo`` is excluded."""
        return repo is not None and repo.lower() in {r.lower() for r in self.exclude_repos}

    def excludes_ticket(self, key: str) -> bool:
        """Whether ticket ``key`` is excluded."""
        return key.upper() in self.exclude_tickets

    def excludes_pr(
        self,
        repo: str,
        number: int,
        opened: datetime | None,
        now: datetime,
        tickets: Iterable[str] = (),
    ) -> bool:
        """Whether PR ``repo#number`` is excluded (by number, age, repo or ticket)."""
        if self.excludes_repo(repo) or any(self.excludes_ticket(t) for t in tickets):
            return True
        for ref in self.exclude_prs:
            if (ref.repo is None or ref.repo.lower() == repo.lower()) and number in ref.numbers:
                return True
        limit = self.exclude_prs_older_than_days
        return bool(limit and opened and (now - opened).days > limit)

    def describe(self) -> list[str]:
        """Short human-readable chips: what the Mac understood."""
        out: list[str] = []
        for ref in self.exclude_prs:
            numbers = ", ".join(f"#{n}" for n in ref.numbers)
            out.append(f"Hide {ref.repo + ' ' if ref.repo else ''}PRs {numbers}")
        if self.exclude_prs_older_than_days:
            out.append(f"Hide PRs open more than {self.exclude_prs_older_than_days} days")
        out.extend(f"Hide ticket {t}" for t in self.exclude_tickets)
        out.extend(f"Hide repo {r}" for r in self.exclude_repos)
        if self.instruction:
            out.append(f"Instruction for the AI: {self.instruction}")
        return out

    @staticmethod
    def merge(rules: Iterable[RuleFilters]) -> RuleFilters:
        """All active rules combined (the strictest age limit wins)."""
        merged = RuleFilters()
        ages: list[int] = []
        instructions: list[str] = []
        for rule in rules:
            merged.exclude_prs.extend(rule.exclude_prs)
            merged.exclude_tickets = sorted({*merged.exclude_tickets, *rule.exclude_tickets})
            merged.exclude_repos = sorted({*merged.exclude_repos, *rule.exclude_repos})
            if rule.exclude_prs_older_than_days:
                ages.append(rule.exclude_prs_older_than_days)
            if rule.instruction:
                instructions.append(rule.instruction)
        merged.exclude_prs_older_than_days = min(ages) if ages else None
        merged.instruction = "\n".join(instructions) or None
        return merged


def _match_repo(text: str, known_repos: Iterable[str]) -> str | None:
    lowered = text.lower()
    for repo in sorted(known_repos, key=len, reverse=True):
        if repo.lower() in lowered:
            return repo
    return None


def interpret_with_patterns(text: str, known_repos: Iterable[str]) -> RuleFilters:
    """Read a rule without an LLM: PR numbers and ranges, ages, ticket keys and repo names."""
    repos = list(known_repos)
    repo = _match_repo(text, repos)
    numbers: set[int] = set()
    for start, end in _PR_RANGE.findall(text):
        first = int(start)
        last = int(end) if end else first
        if 0 < last - first < 100:
            numbers.update(range(first, last + 1))
        else:
            numbers.update({first, last})
    if re.search(r"(?i)\bprs?\b|pull[ -]?request", text):
        numbers.update(int(n) for n in _PR_HASH.findall(text))
    age = _AGE.search(text)
    tickets = _TICKET.findall(text)
    filters = RuleFilters(
        exclude_prs=[PrRef(repo=repo, numbers=sorted(numbers))] if numbers else [],
        exclude_prs_older_than_days=int(age.group(1)) * _UNIT_DAYS[age.group(2).lower()]
        if age
        else None,
        exclude_tickets=tickets,
        exclude_repos=[repo]
        if repo and not numbers and re.search(r"(?i)\brepo(sitory)?\b", text)
        else [],
    )
    if filters.is_empty():
        filters.instruction = text.strip()
    return filters


_SYSTEM = """You turn an engineer's own rule for their daily status digest into filters.
Return JSON with these keys:
- "exclude_prs": [{"repo": <repo name from the list or null>, "numbers": [<PR numbers>]}]
- "exclude_prs_older_than_days": <int or null>
- "exclude_tickets": [<Jira keys like EIT-495>]
- "exclude_repos": [<repo names from the list>]
- "instruction": <anything that is not a filter, as one short sentence, or null>
Use only repo names from the given list. A PR "stack" or ticket mentioned with PRs means those PR numbers.
Return JSON only."""


def interpret(
    text: str, known_repos: Iterable[str], open_prs: list[str], llm: LLMProvider | None
) -> RuleFilters:
    """Read a rule with the LLM, checked against known repos; patterns when there is no LLM."""
    repos = sorted(set(known_repos))
    if llm is None:
        return interpret_with_patterns(text, repos)
    prompt = Prompt(
        system=_SYSTEM,
        user=f"Rule: {text}\n\nRepos: {', '.join(repos) or '-'}\n\nMy pull requests:\n"
        + ("\n".join(open_prs) or "-"),
    )
    try:
        result = llm.complete_json(prompt, RuleFilters)
    except LLMError:
        return interpret_with_patterns(text, repos)
    known = {r.lower(): r for r in repos}
    # Never trust a repo name the model made up.
    result.exclude_repos = [known[r.lower()] for r in result.exclude_repos if r.lower() in known]
    result.exclude_prs = [
        PrRef(
            repo=known.get(ref.repo.lower()) if ref.repo else None, numbers=sorted(set(ref.numbers))
        )
        for ref in result.exclude_prs
        if ref.numbers and (ref.repo is None or ref.repo.lower() in known)
    ]
    if result.is_empty():
        result.instruction = text.strip()
    return result


def active_filters(store: Store) -> RuleFilters:
    """All confirmed (active) rules, merged."""
    rows = store.query("SELECT params FROM my_rules WHERE status = 'active'")
    return RuleFilters.merge(
        RuleFilters.model_validate(json.loads(row["params"] or "{}")) for row in rows
    )


def hidden_session_keys(store: Store) -> set[str]:
    """Sessions the user hid in the app."""
    return {row["session_key"] for row in store.query("SELECT session_key FROM hidden_sessions")}


def rule_context(store: Store, configured_repos: Iterable[str]) -> tuple[list[str], list[str]]:
    """Known repo names and one line per PR, so a rule can name them ("quiz-service PRs 3-7")."""
    repos = set(configured_repos)
    repos.update(r["name"] for r in store.query("SELECT name FROM repos"))
    prs = store.query(
        "SELECT repo, pr_id, title, state, created_at FROM pull_requests ORDER BY repo, pr_id"
    )
    repos.update(r["repo"] for r in prs)
    lines = [
        f'{r["repo"]} #{r["pr_id"]} "{r["title"]}" {r["state"]}, opened {str(r["created_at"])[:10]}'
        for r in prs
    ]
    return sorted(repos), lines
