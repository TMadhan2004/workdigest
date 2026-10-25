"""CLI commands for the digest engine (registered on the main ``digest`` app)."""

from __future__ import annotations

import json
from datetime import date
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape

from workdigest.clock import now_local, work_date
from workdigest.config import Config, ConfigError, load_config
from workdigest.digest.compliance import check_text
from workdigest.digest.draft import Draft, render
from workdigest.digest.repository import ASK_STATUSES, DigestRepository
from workdigest.digest.service import (
    DigestContext,
    add_reply,
    close_day,
    create_draft,
    draft_prompt_text,
    import_history,
    make_provider,
    prepare_inputs,
    read_text_arg,
    regenerate,
    sync_atlassian,
)
from workdigest.integrations.http import HttpError
from workdigest.integrations.sync import make_clients
from workdigest.llm.base import LLMError
from workdigest.paths import Paths
from workdigest.pipeline import run_pass
from workdigest.store.db import Store

console = Console()
history_app = typer.Typer(help="Import previous digests and replies.", no_args_is_help=True)
asks_app = typer.Typer(help="Open asks from the manager.", invoke_without_command=True)
atlassian_app = typer.Typer(help="Read-only Jira and Bitbucket sync.", no_args_is_help=True)

ProviderOpt = Annotated[
    str | None, typer.Option(help="LLM provider: cursor_cli, claude_cli or none (default: config).")
]
DateOpt = Annotated[
    str | None, typer.Option("--date", help="Work day YYYY-MM-DD (default: today).")
]


def _context() -> tuple[Paths, Config, Store]:
    paths = Paths.resolve()
    try:
        config = load_config(paths.config_path)
    except ConfigError as exc:
        console.print(f"[red]Config error:[/red] {escape(str(exc))}")
        raise typer.Exit(2) from exc
    paths.ensure()
    return paths, config, Store.open(paths.db_path)


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        console.print(f"[red]Invalid date {value!r}; expected YYYY-MM-DD[/red]")
        raise typer.Exit(2) from exc


def _day(value: str | None, config: Config) -> date:
    if value is None:
        return work_date(now_local(), config.day.boundary_hour)
    return _parse_date(value)


# ------------------------------------------------------------------ history
@history_app.command("import")
def history_import(
    path: Annotated[str, typer.Argument(help="File in 'Me:' / 'Reply:' format, or - for stdin.")],
) -> None:
    """Import sent digests and replies: seeds plans, asks and standing rules."""
    _, config, store = _context()
    try:
        result = import_history(store, config, read_text_arg(path))
    finally:
        store.close()
    console.print(
        f"Imported {result.digests} digests and {result.replies} replies: "
        f"{result.asks_new} new asks ({result.asks_repeated} repeats merged), {result.rules_new} new standing rules."
    )
    console.print("Review them with `digest asks` and `digest rules`.")


# -------------------------------------------------------------------- reply
def reply(
    path: Annotated[
        str, typer.Argument(help="File with the manager's reply, or - to paste on stdin.")
    ],
    for_date: Annotated[
        str | None, typer.Option("--for", help="Date of the digest this replies to (YYYY-MM-DD).")
    ] = None,
    provider: ProviderOpt = None,
    yes: Annotated[
        bool, typer.Option("--yes", help="Confirm the extracted items without reviewing.")
    ] = False,
) -> None:
    """Parse the manager's reply into asks, questions and standing rules."""
    target = _parse_date(for_date) if for_date else None
    _, config, store = _context()
    try:
        reply_id, parsed, counts = add_reply(
            store, config, read_text_arg(path), target, make_provider(config, provider), confirm=yes
        )
    finally:
        store.close()
    for item in parsed.items:
        flag = " [real action]" if item.needs_real_action else ""
        tickets = f" {item.related_tickets}" if item.related_tickets else ""
        console.print(f"{item.idx:>2} {item.type:<16}{flag}{tickets} {escape(item.text)}")
    if counts:
        console.print(
            f"Confirmed: {counts['asks_new']} new asks, {counts['asks_repeated']} repeats, {counts['rules_new']} new rules."
        )
    else:
        console.print(
            f"Saved as reply {reply_id[:8]}. Re-run with --yes to confirm, or review it in the app (Phase 5)."
        )


# --------------------------------------------------------------------- asks
@asks_app.callback()
def asks_list(
    ctx: typer.Context,
    show_all: Annotated[bool, typer.Option("--all", help="Include closed asks.")] = False,
) -> None:
    """List open asks (oldest first)."""
    if ctx.invoked_subcommand:
        return
    _, _, store = _context()
    try:
        asks = DigestRepository(store).asks(status=None if show_all else "open")
    finally:
        store.close()
    if not asks:
        console.print("No open asks.")
    for ask in asks:
        marks = []
        if ask.needs_real_action:
            marks.append("needs real action")
        if ask.times_raised > 1:
            marks.append(f"raised {ask.times_raised}x")
        meta = f" ({', '.join(marks)})" if marks else ""
        console.print(
            f"{ask.ask_id[:6]} {escape(f'[{ask.status}]')} since {ask.opened_on}{escape(meta)}: {escape(ask.text)}"
        )


@asks_app.command("set")
def asks_set(
    ask_id: Annotated[str, typer.Argument(help="Ask id (prefix is enough).")],
    status: Annotated[str, typer.Argument(help=f"One of {', '.join(ASK_STATUSES)}.")],
    evidence: Annotated[
        str | None, typer.Option(help="Proof for 'done' (link, ticket, commit).")
    ] = None,
    reason: Annotated[str | None, typer.Option(help="Why, for 'disagree' or 'waiting'.")] = None,
) -> None:
    """Change an ask's status."""
    _, _, store = _context()
    try:
        DigestRepository(store).set_ask_status(ask_id, status, evidence=evidence, reason=reason)
    except ValueError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    finally:
        store.close()
    console.print(f"Ask {ask_id} → {status}")


# -------------------------------------------------------------------- rules
def rules() -> None:
    """List the standing rules checked against every draft."""
    _, _, store = _context()
    try:
        repo = DigestRepository(store)
        repo.seed_rules()
        for rule in repo.rules():
            console.print(
                f"{rule.rule_id:<20} {escape(f'[{rule.check_kind}]')} {escape(rule.text)}"
            )
    finally:
        store.close()


# -------------------------------------------------------------------- close
def close(
    day: DateOpt = None,
    rating: Annotated[
        int | None,
        typer.Option(help="Day rating: 1, 3, 7 or 9 (1 = very tough, 9 = super fantastic)."),
    ] = None,
    project: Annotated[str | None, typer.Option(help="Project name for the header.")] = None,
    team: Annotated[
        str | None, typer.Option(help="Comma-separated names you worked with today.")
    ] = None,
) -> None:
    """Close the day: record rating, project and team for the header."""
    _, config, store = _context()
    try:
        close_day(
            store,
            _day(day, config),
            rating=rating,
            project=project,
            team=[t.strip() for t in team.split(",") if t.strip()] if team is not None else None,
        )
    except ValueError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(2) from exc
    finally:
        store.close()
    console.print("Day closed. Run `digest draft` to generate the digest.")


# -------------------------------------------------------------------- draft
def draft(
    day: DateOpt = None,
    reply_file: Annotated[
        str | None,
        typer.Option("--reply", help="Manager's reply to the previous digest (file or -)."),
    ] = None,
    provider: ProviderOpt = None,
    show_input: Annotated[
        bool,
        typer.Option(
            "--show-input", help="Print exactly what would be sent to the LLM, then stop."
        ),
    ] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the draft structure as JSON.")
    ] = False,
    refresh: Annotated[bool, typer.Option(help="Run an ingest pass first.")] = True,
) -> None:
    """Generate today's digest draft with its gap list (never sends anything)."""
    paths, config, store = _context()
    try:
        target = _day(day, config)
        llm = make_provider(config, provider)
        if refresh:
            run_pass(store, config, paths)
            report = sync_atlassian(DigestContext(store, config, paths), target)
            for error in report.errors:
                console.print(f"[yellow]Atlassian sync: {escape(error)}[/yellow]")
        if reply_file:
            previous = DigestRepository(store).latest_digest_before(target)
            for_date = date.fromisoformat(previous[0]) if previous else None
            add_reply(store, config, read_text_arg(reply_file), for_date, llm, confirm=True)
        if show_input:
            ctx = DigestContext(store, config, paths)
            typer.echo(draft_prompt_text(prepare_inputs(ctx, target, llm=None)))
            return
        try:
            digest_id, result = create_draft(DigestContext(store, config, paths), target, llm=llm)
        except LLMError as exc:
            console.print(f"[red]{escape(str(exc))}[/red]")
            raise typer.Exit(1) from exc
    finally:
        store.close()
    _print_draft(digest_id, result, as_json)


def section(
    key: Annotated[
        str, typer.Argument(help="Section key, e.g. risks, learnings, in_short, closing.")
    ],
    day: DateOpt = None,
    provider: ProviderOpt = None,
) -> None:
    """Regenerate one section of the latest draft without touching the rest."""
    paths = Paths.resolve()
    try:
        config = load_config(paths.config_path)
    except ConfigError as exc:
        console.print(f"[red]Config error:[/red] {escape(str(exc))}")
        raise typer.Exit(2) from exc
    llm = make_provider(config, provider)
    if llm is None:
        console.print("[red]Regenerating needs an LLM provider (cursor_cli or claude_cli).[/red]")
        raise typer.Exit(2)
    _, _, store = _context()
    try:
        digest_id, result = regenerate(
            DigestContext(store, config, paths), _day(day, config), key, llm=llm
        )
    except (LookupError, ValueError, LLMError) as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    finally:
        store.close()
    _print_draft(digest_id, result, as_json=False)


def check(path: Annotated[str, typer.Argument(help="Digest text file to check, or -.")]) -> None:
    """Run the standing-rules compliance check on any digest text."""
    _, _, store = _context()
    try:
        repo = DigestRepository(store)
        repo.seed_rules()
        results = check_text(read_text_arg(path), repo.rules())
    finally:
        store.close()
    failed = 0
    for result in results:
        mark = {True: "pass", False: "FAIL", None: "manual"}[result.passed]
        failed += int(result.passed is False)
        console.print(f"{mark:<6} {result.rule_id:<20} {escape(result.message or result.text)}")
    raise typer.Exit(1 if failed else 0)


def _print_draft(digest_id: str, result: Draft, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(json.loads(result.model_dump_json()), indent=2, ensure_ascii=False))
        return
    typer.echo(render(result))
    console.rule(
        f"Gaps to confirm ({len(result.gaps)}) · draft {digest_id[:8]} · provider {result.provider}"
    )
    order = {"real_action": 0, "rule": 1, "ask": 2, "unverified": 3, "missing": 4, "input": 5}
    for gap in sorted(result.gaps, key=lambda g: order.get(g.kind, 9)):
        where = f" [{gap.section}]" if gap.section else ""
        console.print(f"- {gap.kind}{escape(where)}: {escape(gap.message)}")


# ---------------------------------------------------------------- atlassian
@atlassian_app.command("check")
def atlassian_check() -> None:
    """Verify both tokens: who they belong to, and whether reads work."""
    paths = Paths.resolve()
    config = load_config(paths.config_path)
    clients = make_clients(config)
    failed = False
    if clients.jira is None:
        console.print("Jira: no token (`digest secrets set jira`)")
        failed = True
    else:
        try:
            me = clients.jira.myself()
            console.print(
                f"Jira: OK as {escape(str(me.get('displayName')))} via {clients.jira.base.split('/ex/jira/')[1][:8]}…"
            )
        except HttpError as exc:
            console.print(f"[red]Jira: {escape(str(exc))}[/red]")
            failed = True
    if clients.bitbucket is None:
        console.print("Bitbucket: no token (`digest secrets set bitbucket`)")
        failed = True
    else:
        try:
            user = clients.bitbucket.user()
            console.print(
                f"Bitbucket: OK as {escape(str(user.get('display_name')))} in {config.atlassian.bitbucket_workspace}"
            )
        except HttpError as exc:
            console.print(f"[red]Bitbucket: {escape(str(exc))}[/red]")
            failed = True
    raise typer.Exit(1 if failed else 0)


@atlassian_app.command("sync")
def atlassian_sync(day: DateOpt = None) -> None:
    """Pull the day's Jira tickets, comments, status changes and my PRs."""
    paths, config, store = _context()
    try:
        report = sync_atlassian(DigestContext(store, config, paths), _day(day, config))
    finally:
        store.close()
    console.print(
        f"Jira: {report.jira_tickets} tickets, {report.jira_events} events; In Progress: {', '.join(report.in_progress) or '-'}; "
        f"missing: {', '.join(report.missing_keys) or '-'}. Bitbucket: {report.pull_requests} PRs."
    )
    for skipped in report.skipped:
        console.print(f"skipped {escape(skipped)}")
    for error in report.errors:
        console.print(f"[red]{escape(error)}[/red]")
    raise typer.Exit(1 if report.errors else 0)


def register(app: typer.Typer) -> None:
    """Attach the digest commands to the main CLI."""
    app.add_typer(history_app, name="history")
    app.add_typer(asks_app, name="asks")
    app.add_typer(atlassian_app, name="atlassian")
    app.command("reply")(reply)
    app.command("rules")(rules)
    app.command("close")(close)
    app.command("draft")(draft)
    app.command("section")(section)
    app.command("check")(check)
