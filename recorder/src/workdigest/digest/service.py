"""Use cases that wire the store, config and digest engine (shared by the CLI and later the backend)."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path

from workdigest.clock import now_local
from workdigest.config import Config
from workdigest.digest.document import parse_history
from workdigest.digest.draft import Draft, Gap, render
from workdigest.digest.evidence import EvidencePack, build_pack
from workdigest.digest.generator import (
    DraftInputs,
    build_prompt,
    generate_draft,
    regenerate_section,
)
from workdigest.digest.my_rules import active_filters
from workdigest.digest.plan import active_plan, plan_items
from workdigest.digest.reply_parser import ParsedReply, parse_reply
from workdigest.digest.repository import DigestRepository
from workdigest.digest.summarizer import summarize_day
from workdigest.digest.template import Template, load_template
from workdigest.ingest.context import TicketMatcher
from workdigest.integrations.sync import (
    Clients,
    SyncReport,
    in_progress_on,
    make_clients,
    missing_on,
    sync_day,
    synced,
    ticket_hygiene,
)
from workdigest.llm.base import LLMProvider
from workdigest.llm.cli_providers import ClaudeCliProvider, CursorCliProvider
from workdigest.paths import Paths
from workdigest.pipeline import build_factory
from workdigest.report.today import day_evidence
from workdigest.store.db import Store


def make_provider(config: Config, override: str | None = None) -> LLMProvider | None:
    """LLM provider from config (``none`` or ``transmit.llm_enabled = false`` gives no LLM)."""
    name = override or config.digest.provider
    if name == "none" or not config.transmit.llm_enabled:
        return None
    if name == "claude_cli":
        return ClaudeCliProvider(model=config.digest.model, timeout_s=config.digest.timeout_s)
    if name == "cursor_cli":
        return CursorCliProvider(model=config.digest.model, timeout_s=config.digest.timeout_s)
    raise ValueError(f"unknown provider {name!r}")


@dataclass(slots=True)
class ImportResult:
    """Counters for a history import."""

    digests: int = 0
    replies: int = 0
    asks_new: int = 0
    asks_repeated: int = 0
    rules_new: int = 0


def import_history(store: Store, config: Config, text: str) -> ImportResult:
    """Import sent digests and the manager's replies (``Me:`` / ``Reply:`` format)."""
    repo = DigestRepository(store)
    repo.seed_rules()
    tickets = TicketMatcher(config.jira.project_keys)
    result = ImportResult()
    with store.transaction():
        for pair in parse_history(text):
            day = pair.digest.work_date
            if day is None:
                continue
            repo.save_digest(day, pair.digest.text, origin="imported", status="final")
            items = plan_items(pair.digest)
            if items:
                repo.save_plan(day, items)
            result.digests += 1
            if pair.reply:
                parsed = parse_reply(pair.reply, tickets, day)
                reply_id = repo.save_reply(pair.reply, day, parsed)
                counts = repo.confirm_reply(reply_id, day, parsed.items)
                result.replies += 1
                result.asks_new += counts["asks_new"]
                result.asks_repeated += counts["asks_repeated"]
                result.rules_new += counts["rules_new"]
    return result


def add_reply(
    store: Store,
    config: Config,
    text: str,
    for_date: date | None,
    llm: LLMProvider | None,
    *,
    confirm: bool,
    reply_id: str | None = None,
) -> tuple[str, ParsedReply, dict[str, int] | None]:
    """Parse and store a pasted reply; optionally confirm it straight away."""
    repo = DigestRepository(store)
    repo.seed_rules()
    parsed = parse_reply(text, TicketMatcher(config.jira.project_keys), for_date, llm)
    with store.transaction():
        reply_id = repo.save_reply(text, for_date, parsed, reply_id=reply_id)
        counts = (
            repo.confirm_reply(reply_id, for_date or now_local().date(), parsed.items)
            if confirm
            else None
        )
    return reply_id, parsed, counts


def day_state(store: Store, day: date) -> dict[str, object]:
    """Rating, project and team recorded for ``day``."""
    rows = store.query("SELECT * FROM day_state WHERE work_date = ?", (day.isoformat(),))
    if not rows:
        return {}
    row = rows[0]
    return {
        "rating": row["rating"],
        "project": row["project"],
        "team_members": json.loads(row["team_members"]),
        "closed_at": row["closed_at"],
    }


def close_day(
    store: Store,
    day: date,
    *,
    rating: int | None = None,
    project: str | None = None,
    team: list[str] | None = None,
) -> None:
    """Mark the day closed and record rating/project/team (each only when given)."""
    if rating is not None and rating not in {1, 3, 7, 9}:
        raise ValueError("day rating must be 1, 3, 7 or 9")
    current = day_state(store, day)
    store.conn.execute(
        """
        INSERT INTO day_state (work_date, closed_at, rating, project, team_members) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (work_date) DO UPDATE SET
            closed_at = excluded.closed_at, rating = excluded.rating,
            project = excluded.project, team_members = excluded.team_members
        """,
        (
            day.isoformat(),
            now_local().isoformat(),
            rating if rating is not None else current.get("rating"),
            project if project is not None else current.get("project"),
            json.dumps(team if team is not None else current.get("team_members", [])),
        ),
    )


@dataclass(frozen=True, slots=True)
class DigestContext:
    """The store, configuration and paths one digest operation works with."""

    store: Store
    config: Config
    paths: Paths


def prepare_inputs(
    ctx: DigestContext,
    day: date,
    *,
    llm: LLMProvider | None,
    now: datetime | None = None,
) -> DraftInputs:
    """Collect evidence, summaries, plan, asks and rules for ``day``."""
    store, config, paths = ctx.store, ctx.config, ctx.paths
    current = now or now_local()
    repo = DigestRepository(store)
    repo.seed_rules()
    template = _template(paths, config)
    evidence = day_evidence(store, day.isoformat(), current, config.day.idle_timeout_minutes)
    factory = build_factory(config)
    summaries = summarize_day(
        store,
        day.isoformat(),
        redactor=factory.redactor,
        boundary_hour=config.day.boundary_hour,
        llm=llm,
        transmit=config.transmit,
    )
    plan = active_plan(repo.plans(), day)
    open_asks = repo.asks(status="open")
    # The closing reports asks answered since the previous digest (any version), once each.
    previous_any = repo.latest_digest_before(day, final_only=False)
    closed_asks = repo.closed_asks(previous_any[0] if previous_any else None, day.isoformat())
    titles = repo.known_ticket_titles()
    filters = active_filters(store)
    pack: EvidencePack = build_pack(
        store,
        evidence,
        summaries,
        ticket_titles=titles,
        plan=plan,
        closed_asks=closed_asks,
        commit_subjects=config.transmit.commit_subjects,
        now=current,
        filters=filters,
    )
    previous = repo.latest_digest_before(day)
    state = day_state(store, day)
    stored_team = state.get("team_members")
    team = (
        [str(t) for t in stored_team]
        if isinstance(stored_team, list) and stored_team
        else list(config.digest.team_members)
    )
    team_source = None
    if not team and previous:
        carried = previous[1].field_value("team members")
        if carried:
            team = [name.strip() for name in carried.split(",") if name.strip()]
            team_source = f"carried from {previous[0]}"
    project = (
        state.get("project")
        or config.digest.project
        or template.project
        or (previous[1].field_value("project") if previous else None)
    )
    deadline_h, deadline_m = (int(x) for x in config.digest.send_deadline.split(":"))
    return DraftInputs(
        work_date=day,
        template=template,
        pack=pack,
        plan=plan,
        open_asks=open_asks,
        rules=repo.rules(),
        ticket_titles=titles,
        project=str(project) if project else None,
        team_members=team,
        team_source=team_source,
        rating=state.get("rating"),  # type: ignore[arg-type]
        previous_in_short=previous[1].field_value("in short") if previous else None,
        send_deadline=time(deadline_h, deadline_m),
        now=current,
        atlassian_synced=synced(store),
        instructions=filters.instruction,
        hygiene_gaps=hygiene_gaps(store, day, pack.tickets) if synced(store) else None,
    )


def hygiene_gaps(store: Store, day: date, tickets: list[str]) -> list[Gap]:
    """Ticket hygiene from synced Jira data (standing rules R11 and R12)."""
    gaps: list[Gap] = []
    for check in ticket_hygiene(store, day, tickets):
        if not check.has_branch:
            gaps.append(
                Gap(
                    kind="real_action",
                    ref="R11-ticket-hygiene",
                    message=f"{check.key} has no branch created from it (name a branch after the key)",
                )
            )
        missing = [
            label
            for label, present in (("due date", check.has_due_date), ("parent", check.has_parent))
            if present is False
        ]
        if missing:
            gaps.append(
                Gap(
                    kind="real_action",
                    ref="R11-ticket-hygiene",
                    message=f"{check.key} has no {' and no '.join(missing)} in Jira; fill {' and '.join(missing)}",
                )
            )
        if not check.commented_today:
            gaps.append(
                Gap(
                    kind="real_action",
                    ref="R11-ticket-hygiene",
                    message=f"{check.key} has no Activity comment from you today; add what moved, what was verified, what is pending",
                )
            )
    current = in_progress_on(store, day) or []
    # Priya's rule counts the status named "In Progress"; Jira's category also holds Review and
    # Testing (matched against his 23 Oct reply: exactly EIT-9333, 9334, 6698, 2269, 1115).
    active = [key for key, status in current if status.strip().lower() == "in progress"]
    waiting = [
        f"{key} ({status})" for key, status in current if status.strip().lower() != "in progress"
    ]
    if len(active) > 1:
        gaps.append(
            Gap(
                kind="real_action",
                ref="R12-one-in-progress",
                message=f"{len(active)} tickets are In Progress: {', '.join(active)}; keep one and move the others",
            )
        )
    if waiting:
        more = " …" if len(waiting) > 8 else ""
        gaps.append(
            Gap(
                kind="input",
                ref="R12-one-in-progress",
                message=f"{len(waiting)} more of your tickets are in Review/Testing: {', '.join(waiting[:8])}{more}",
            )
        )
    for key in missing_on(store, day):
        gaps.append(
            Gap(
                kind="real_action",
                ref="jira-missing",
                message=f"{key} does not open in Jira (missing or no access); check the key",
            )
        )
    return gaps


def sync_atlassian(ctx: DigestContext, day: date, clients: Clients | None = None) -> SyncReport:
    """Read the day's Jira and Bitbucket facts (no-op for a side whose token is missing)."""
    factory = build_factory(ctx.config)
    return sync_day(
        ctx.store, ctx.config, clients or make_clients(ctx.config), day, factory.redactor
    )


def _template(paths: Paths, config: Config) -> Template:
    template = load_template(paths.home / "template.toml")
    # The name in config.toml (set in `digest setup`) wins over the one learned from greetings.
    manager = config.digest.manager_name or template.manager_name
    return template.model_copy(update={"manager_name": manager})


def draft_prompt_text(inputs: DraftInputs) -> str:
    """Exactly what would be sent to the LLM for a full draft."""
    prompt = build_prompt(inputs)
    return f"=== SYSTEM ===\n{prompt.system}\n\n=== USER ===\n{prompt.user}"


def create_draft(
    ctx: DigestContext,
    day: date,
    *,
    llm: LLMProvider | None,
    now: datetime | None = None,
) -> tuple[str, Draft]:
    """Generate, store and return a draft (``digest_id``, draft)."""
    inputs = prepare_inputs(ctx, day, llm=llm, now=now)
    draft = generate_draft(inputs, llm)
    return _store_draft(ctx.store, draft), draft


def regenerate(
    ctx: DigestContext,
    day: date,
    key: str,
    *,
    llm: LLMProvider,
    now: datetime | None = None,
) -> tuple[str, Draft]:
    """Regenerate one section of the latest draft for ``day``."""
    rows = ctx.store.query(
        "SELECT structure FROM digests WHERE work_date = ? AND origin IN ('generated', 'edited') "
        "ORDER BY created_at DESC LIMIT 1",
        (day.isoformat(),),
    )
    if not rows:
        raise LookupError(f"no draft for {day}; run `digest draft` first")
    draft = Draft.model_validate_json(rows[0]["structure"])
    inputs = prepare_inputs(ctx, day, llm=llm, now=now)
    updated = regenerate_section(draft, key, inputs, llm)
    return _store_draft(ctx.store, updated), updated


def _store_draft(store: Store, draft: Draft) -> str:
    repo = DigestRepository(store)
    text = render(draft)
    with store.transaction():
        digest_id = repo.save_digest(
            draft.work_date,
            text,
            origin="generated",
            status="draft",
            template_version=draft.template_version,
            structure=json.loads(draft.model_dump_json()),
            gaps=[g.model_dump() for g in draft.gaps],
        )
        rows = []
        for index, (section, statement) in enumerate(draft.all_statements()):
            rows.append(
                {
                    "section_key": section,
                    "idx": index,
                    "text": statement.text,
                    "verified": statement.verified,
                    "evidence_ids": statement.evidence_ids,
                    "problems": statement.problems,
                }
            )
        repo.save_statements(digest_id, rows)
    return digest_id


def read_text_arg(value: str) -> str:
    """Read a file path argument, or stdin for ``-``."""
    if value == "-":
        return sys.stdin.read()
    return Path(value).expanduser().read_text(encoding="utf-8")
