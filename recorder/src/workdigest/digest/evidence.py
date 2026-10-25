"""Build the evidence pack: the numbered facts and claims a digest may cite.

IDs: ``K`` ticket, ``C`` commit, ``B`` new branch, ``T`` test suite (last run of the day),
``S`` session summary (claim), ``P`` plan item, ``A`` open ask, ``M`` reply item.

Only what is needed goes to the LLM: one line per item, already redacted, with no raw
transcripts and no diffs. ``EvidencePack.render()`` is exactly the text that is sent, and
``digest draft --show-input`` prints it.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import quote

from workdigest.clock import local_tz, parse_iso
from workdigest.collectors.git_poller import branch_url
from workdigest.digest.draft import EvidenceItem
from workdigest.digest.my_rules import RuleFilters
from workdigest.digest.plan import ActivePlan
from workdigest.digest.repository import Ask
from workdigest.digest.summarizer import SessionSummary, summary_line
from workdigest.integrations.bitbucket import human_duration
from workdigest.report.today import DayEvidence, TestRunRow, is_backup_branch
from workdigest.store.db import Store


@dataclass(slots=True)
class EvidencePack:
    """All evidence for one work day, keyed by id."""

    items: dict[str, EvidenceItem] = field(default_factory=dict)
    tickets: list[str] = field(default_factory=list)
    tentative_tickets: list[str] = field(default_factory=list)
    # Branches of PRs that My rules exclude: they must not resurface as "branches with no PR".
    excluded_branches: set[str] = field(default_factory=set)

    def add(self, prefix: str, **fields: object) -> EvidenceItem:
        """Add an item with the next id for ``prefix``."""
        number = (
            sum(1 for key in self.items if key.startswith(prefix) and key[len(prefix) :].isdigit())
            + 1
        )
        item = EvidenceItem(id=f"{prefix}{number}", **fields)
        self.items[item.id] = item
        return item

    def of_kind(self, kind: str) -> list[EvidenceItem]:
        """Items of one kind in id order."""
        return [item for item in self.items.values() if item.kind == kind]

    def render(self) -> str:
        """Text sent to the LLM."""
        groups = (
            ("Tickets", "ticket"),
            ("Jira activity by me today (facts)", "jira"),
            ("My pull requests (Bitbucket, facts)", "pull_request"),
            ("Commits (git, facts)", "commit"),
            ("New branches (git, facts)", "branch"),
            ("Test suites, last run today (facts)", "test_run"),
            ("Agent sessions (claims: what the agents reported)", "session"),
            ("Active five-day plan", "plan"),
        )
        out: list[str] = []
        for title, kind in groups:
            items = self.of_kind(kind)
            if items:
                out.append(f"## {title}")
                out.extend(f"{item.id}: {item.text}" for item in items)
                out.append("")
        return "\n".join(out).strip()


def _suite_key(run: TestRunRow) -> tuple[str, str]:
    return (run.repo or "unknown-repo", run.runner)


def build_pack(
    store: Store,
    evidence: DayEvidence,
    summaries: dict[str, SessionSummary],
    *,
    ticket_titles: dict[str, str],
    plan: ActivePlan | None,
    closed_asks: list[Ask],
    min_prompt_mentions: int = 2,
    commit_subjects: bool = True,
    now: datetime | None = None,
    filters: RuleFilters | None = None,
) -> EvidencePack:
    """Assemble the evidence pack for one day (after applying My rules)."""
    rules = filters or RuleFilters()
    pack = EvidencePack()
    evidence = _apply_rules(evidence, rules)
    web_urls = {r["name"]: r["web_url"] for r in store.query("SELECT name, web_url FROM repos")}
    _add_tickets(pack, store, evidence, ticket_titles, min_prompt_mentions, rules=rules)
    _add_commits(pack, evidence, web_urls, commit_subjects=commit_subjects)
    _add_branches(pack, evidence)
    _add_tests(pack, evidence)
    _add_jira_activity(pack, store, evidence.work_date, rules)
    _add_pull_requests(pack, store, now or datetime.now(tz=local_tz()), rules)
    _add_sessions(pack, evidence, summaries)
    _add_context(pack, plan, closed_asks)
    return pack


def _apply_rules(evidence: DayEvidence, rules: RuleFilters) -> DayEvidence:
    """Drop everything My rules exclude: repos, and work on excluded tickets."""
    if rules.is_empty():
        return evidence

    def keep(repo: str | None, tickets: list[str]) -> bool:
        if rules.excludes_repo(repo):
            return False
        # Work that is only about excluded tickets goes; work that also touches others stays.
        return not tickets or not all(rules.excludes_ticket(t) for t in tickets)

    sessions = [
        s.model_copy(update={"tickets": [t for t in s.tickets if not rules.excludes_ticket(t)]})
        for s in evidence.sessions
        if not (s.repos and all(rules.excludes_repo(r) for r in s.repos)) and keep(None, s.tickets)
    ]
    return evidence.model_copy(
        update={
            "sessions": sessions,
            "commits": [c for c in evidence.commits if keep(c.repo, c.tickets)],
            "test_runs": [t for t in evidence.test_runs if not rules.excludes_repo(t.repo)],
            "branches_created": [
                b
                for b in evidence.branches_created
                if keep(str(b.get("repo") or ""), list(b.get("tickets", [])))
            ],
            "tickets": [t for t in evidence.tickets if not rules.excludes_ticket(t)],
        }
    )


def _add_jira_activity(pack: EvidencePack, store: Store, day: str, rules: RuleFilters) -> None:
    """My Jira comments and status changes today (facts, comment text redacted at sync)."""
    rows = store.query(
        "SELECT key, kind, ts, detail FROM ticket_events WHERE work_date = ? AND by_me = 1 ORDER BY ts",
        (day,),
    )
    for row in rows:
        if rules.excludes_ticket(row["key"]):
            continue
        detail = json.loads(row["detail"])
        if row["kind"] == "comment":
            text = f'{row["key"]} Jira comment by me at {row["ts"][11:16]}: "{detail.get("excerpt", "")}"'
        else:
            text = f"{row['key']} Jira {detail.get('field')} changed by me at {row['ts'][11:16]}: {detail.get('from')} → {detail.get('to')}"
        pack.add("J", kind="jira", strength="fact", text=text, refs={"key": row["key"]})


def _add_pull_requests(pack: EvidencePack, store: Store, now: datetime, rules: RuleFilters) -> None:
    """My PRs that are open or were updated recently, with review turnaround."""
    for pr in store.query("SELECT * FROM pull_requests ORDER BY repo, pr_id"):
        created = parse_iso(pr["created_at"])
        if created is None:
            continue
        tickets = json.loads(pr["ticket_keys"] or "[]")
        if rules.excludes_pr(pr["repo"], int(pr["pr_id"]), created, now, tickets):
            if pr["source_branch"]:
                pack.excluded_branches.add(pr["source_branch"])
            continue
        line = f'{pr["repo"]} PR #{pr["pr_id"]} "{pr["title"]}" {pr["source_branch"]} → {pr["dest_branch"]}, {pr["state"]}, opened {created:%d %b %H:%M}'
        if pr["first_approved_at"] and (approved := parse_iso(pr["first_approved_at"])):
            names = ", ".join(json.loads(pr["approved_by"]))
            line += f"; first approved {approved:%d %b %H:%M} by {names} ({human_duration(created, approved)} after opening)"
        if pr["declined_at"] and (declined := parse_iso(pr["declined_at"])):
            line += f"; declined {declined:%d %b %H:%M} ({human_duration(created, declined)} after opening)"
        if pr["merged_at"] and (merged := parse_iso(pr["merged_at"])):
            line += f"; merged {merged:%d %b %H:%M}"
        if pr["state"] == "OPEN" and not pr["first_approved_at"]:
            line += f"; no approval yet, open for {human_duration(created, now)}"
        pack.add(
            "R",
            kind="pull_request",
            strength="fact",
            text=line,
            refs={"url": pr["url"], "repo": pr["repo"], "branch": pr["source_branch"] or ""},
        )


def _add_tickets(
    pack: EvidencePack,
    store: Store,
    evidence: DayEvidence,
    ticket_titles: dict[str, str],
    min_prompt_mentions: int,
    *,
    rules: RuleFilters,
) -> None:
    """Facts (commit/branch) first, then tickets mentioned repeatedly in prompts."""
    fact_tickets: dict[str, None] = {}
    for commit in evidence.commits:
        fact_tickets.update(dict.fromkeys(commit.tickets))
    for created in evidence.branches_created:
        fact_tickets.update(dict.fromkeys(created.get("tickets", [])))
    mentions: dict[str, int] = defaultdict(int)
    for row in store.query(
        "SELECT ticket_keys FROM events WHERE work_date = ? AND kind = 'prompt' "
        "AND (session_key IS NULL OR session_key NOT IN (SELECT session_key FROM hidden_sessions))",
        (evidence.work_date,),
    ):
        for key in json.loads(row["ticket_keys"]):
            mentions[key] += 1
    for row in store.query(
        "SELECT DISTINCT key FROM ticket_events WHERE work_date = ? AND by_me = 1",
        (evidence.work_date,),
    ):
        fact_tickets.setdefault(row["key"], None)  # I commented or changed it in Jira today
    session_tickets = {key for s in evidence.sessions for key in s.tickets}
    for key in list(fact_tickets) + sorted(session_tickets - set(fact_tickets)):
        if rules.excludes_ticket(key):
            continue
        is_fact = key in fact_tickets
        if not is_fact and mentions.get(key, 0) < min_prompt_mentions:
            pack.tentative_tickets.append(key)
            continue
        pack.tickets.append(key)
        commits = sum(1 for c in evidence.commits if key in c.tickets)
        pack.add(
            "K",
            kind="ticket",
            strength="fact" if is_fact else "claim",
            text=f"{key} – {ticket_titles.get(key) or '(title unknown)'}; commits today: {commits}; "
            f"prompt mentions: {mentions.get(key, 0)}{_jira_facts(store, key, evidence.work_date)}",
            refs={"key": key},
        )


def _jira_facts(store: Store, key: str, day: str) -> str:
    """Status, due date, parent and my Jira activity today, when Jira has been synced."""
    rows = store.query(
        "SELECT status, due_date, parent_key, issue_type FROM tickets WHERE key = ?", (key,)
    )
    if not rows:
        return ""
    row = rows[0]
    parts = [f"Jira status {row['status'] or '?'}", f"type {row['issue_type'] or '?'}"]
    parts.append(f"due {row['due_date']}" if row["due_date"] else "no due date")
    parts.append(f"parent {row['parent_key']}" if row["parent_key"] else "no parent")
    events = store.query(
        "SELECT kind, detail FROM ticket_events WHERE key = ? AND work_date = ? AND by_me = 1 ORDER BY ts",
        (key, day),
    )
    comments = sum(1 for e in events if e["kind"] == "comment")
    moves = [json.loads(e["detail"]) for e in events if e["kind"] == "status_change"]
    parts.append(f"my comments today: {comments}")
    if moves:
        parts.append("status moved today: " + ", ".join(f"{m['from']} → {m['to']}" for m in moves))
    return "; " + "; ".join(parts)


def _add_commits(
    pack: EvidencePack,
    evidence: DayEvidence,
    web_urls: dict[str, str | None],
    *,
    commit_subjects: bool,
) -> None:
    for commit in evidence.commits:
        live = [b for b in commit.branches if not is_backup_branch(b)]
        branch = (live or commit.branches or [""])[0]
        link = branch_url(web_urls.get(commit.repo), branch) if branch else None
        subject = commit.subject if commit_subjects else "(subject withheld)"
        pack.add(
            "C",
            kind="commit",
            strength="fact",
            text=(
                f'{commit.repo} [{branch or "no local branch"}] {commit.sha[:8]} "{subject}" '
                f"files {commit.files_changed}, +{commit.insertions}/-{commit.deletions}, "
                f"{'pushed' if commit.pushed else 'NOT pushed'}"
                + (f", tickets {', '.join(commit.tickets)}" if commit.tickets else "")
            ),
            refs={
                "sha": commit.sha,
                "repo": commit.repo,
                "branch": branch,
                **({"url": link} if link else {}),
            },
        )


def _add_branches(pack: EvidencePack, evidence: DayEvidence) -> None:
    for created in evidence.branches_created:
        pack.add(
            "B",
            kind="branch",
            strength="fact",
            text=f"{created['repo']} new branch {created['branch']} at {str(created['ts'])[11:16]}",
            refs={
                "branch": str(created["branch"]),
                **({"url": str(created["url"])} if created.get("url") else {}),
            },
        )


def _add_tests(pack: EvidencePack, evidence: DayEvidence) -> None:
    runs_by_suite: dict[tuple[str, str], list[TestRunRow]] = defaultdict(list)
    for run in evidence.test_runs:
        runs_by_suite[_suite_key(run)].append(run)
    for (repo, runner), runs in sorted(runs_by_suite.items()):
        # Report the latest *full* run: agents often re-run one file (25/25) after the whole
        # suite (650/650), and the subset must not hide or replace the suite result.
        largest = max(r.total for r in runs)
        last = [r for r in runs if r.total >= 0.9 * largest][-1]
        earlier_failures = sum(1 for r in runs if r.failed and r is not last)
        pack.add(
            "T",
            kind="test_run",
            strength="fact",
            text=(
                f"{repo} {runner}: {last.passed}/{last.total} {last.unit} passed"
                + (f", {last.failed} failed" if last.failed else "")
                + (f", {last.skipped} skipped" if last.skipped else "")
                + f" at {last.ts[11:16]} ({len(runs)} runs today"
                + (f", {earlier_failures} earlier runs had failures" if earlier_failures else "")
                + f'); summary line: "{last.summary_line}"'
            ),
            refs={"command": last.command[:160], "summary_line": last.summary_line},
        )


def _add_sessions(
    pack: EvidencePack, evidence: DayEvidence, summaries: dict[str, SessionSummary]
) -> None:
    for session in evidence.sessions:
        summary = summaries.get(session.session_key)
        detail = summary_line(summary) if summary else ""
        tool = "Claude Code" if session.source == "claude_code" else "Cursor"
        name = session.title or (summary.headline if summary else "") or session.session_key[-8:]
        pack.add(
            "S",
            kind="session",
            strength="claim",
            text=(
                f'{tool} session "{name}" {session.first_ts[11:16]}-{session.last_ts[11:16]}, '
                f"repos {', '.join(session.repos) or '-'}, {session.prompts} prompts, "
                f"{session.tool_uses + session.shell_runs} tool calls, {session.file_edits} edits"
                + (f"; tickets {', '.join(session.tickets)}" if session.tickets else "")
                + (f". {detail}" if detail else "")
            ),
            refs={"session": session.session_key},
        )


def _add_context(pack: EvidencePack, plan: ActivePlan | None, closed_asks: list[Ask]) -> None:
    if plan is not None:
        for index, text in enumerate(plan.items, start=1):
            pack.add(
                "P",
                kind="plan",
                strength="context",
                text=f"[{plan.plan_date:%d %b} plan, item {index}, today is day {plan.day} of {plan.horizon}] {text}",
            )
    # Only asks you answered (Done with evidence, Disagree with a reason) are reported; code
    # writes the closing from these. Pending asks never reach the digest.
    for ask in closed_asks:
        answer = (
            f"done; evidence: {ask.evidence}"
            if ask.status == "done"
            else f"I disagree; reason: {ask.reason}"
        )
        refs = {"ask_id": ask.ask_id, "status": ask.status}
        if ask.status == "done" and ask.evidence and ask.evidence.startswith("http"):
            refs["url"] = ask.evidence
        pack.add("A", kind="ask", strength="fact", text=f"{ask.text} → {answer}", refs=refs)


def jira_url(base: str, key: str) -> str:
    """Browse URL for a ticket."""
    return base.rstrip("/") + "/" + quote(key)
