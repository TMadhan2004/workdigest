"""``digest`` command-line interface."""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from workdigest import cloud_cli, digest_cli, service_cli, setup_cli
from workdigest.clock import now_local, work_date
from workdigest.config import Config, ConfigError, load_config, render_default_config
from workdigest.hooks import installer
from workdigest.paths import Paths
from workdigest.pipeline import STAGES, run_pass
from workdigest.report.today import DayEvidence, day_evidence
from workdigest.secrets import KNOWN_SECRETS, SecretStore
from workdigest.secrets import SERVICE as SECRET_SERVICE
from workdigest.service import LaunchAgent
from workdigest.store.db import Store

app = typer.Typer(help="WorkDigest: passive work recorder and daily digest.", no_args_is_help=True)
hooks_app = typer.Typer(help="Install, remove or inspect agent hooks.", no_args_is_help=True)
app.add_typer(hooks_app, name="hooks")
secrets_app = typer.Typer(help="Credentials in the macOS Keychain.", no_args_is_help=True)
app.add_typer(secrets_app, name="secrets")
console = Console()

DEFAULT_CLAUDE_SETTINGS = Path("~/.claude/settings.json").expanduser()
DEFAULT_CURSOR_HOOKS = Path("~/.cursor/hooks.json").expanduser()
GIT_POLL_EVERY_S = 300
TRANSCRIPT_EVERY_S = 300


def _paths() -> Paths:
    return Paths.resolve()


def _config(paths: Paths) -> Config:
    try:
        return load_config(paths.config_path)
    except ConfigError as exc:
        console.print(f"[red]Config error:[/red] {exc}")
        raise typer.Exit(2) from exc


def _store(paths: Paths) -> Store:
    paths.ensure()
    return Store.open(paths.db_path)


# --------------------------------------------------------------------- init
@app.command()
def init(
    name: Annotated[str, typer.Option(help="Your name as shown in git.")] = "",
    email: Annotated[
        list[str] | None, typer.Option(help="Your git author email (repeatable).")
    ] = None,
) -> None:
    """Create the WorkDigest home folder and a starter config.toml."""
    paths = _paths()
    paths.ensure()
    if paths.config_path.exists():
        console.print(f"Config already exists: {paths.config_path}")
        return
    paths.config_path.write_text(render_default_config(name, email or []), encoding="utf-8")
    paths.config_path.chmod(0o600)
    console.print(
        f"Wrote {paths.config_path}. Add \\[\\[repos]] entries, then run `digest doctor`."
    )


# -------------------------------------------------------------------- hooks
@hooks_app.command("install")
def hooks_install(
    claude: Annotated[bool, typer.Option(help="Install Claude Code hooks.")] = True,
    cursor: Annotated[bool, typer.Option(help="Install Cursor hooks.")] = True,
    claude_settings: Annotated[
        Path, typer.Option(help="Claude Code settings file.")
    ] = DEFAULT_CLAUDE_SETTINGS,
    cursor_hooks: Annotated[Path, typer.Option(help="Cursor hooks file.")] = DEFAULT_CURSOR_HOOKS,
    python: Annotated[
        str | None, typer.Option(help="Interpreter for the hook (default: python3 on PATH).")
    ] = None,
) -> None:
    """Copy the hook script and register it (backs up each settings file first)."""
    paths = _paths()
    try:
        interpreter = python or installer.default_python()
    except installer.InstallError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    script = installer.install_hook_script(paths)
    console.print(f"Hook script: {script}")
    try:
        if claude:
            backup = installer.install_claude(
                claude_settings, installer.hook_command(interpreter, script, "claude_code")
            )
            console.print(
                f"Claude Code hooks installed in {claude_settings}"
                + (f" (backup: {backup})" if backup else "")
            )
        if cursor:
            backup = installer.install_cursor(
                cursor_hooks, installer.hook_command(interpreter, script, "cursor")
            )
            console.print(
                f"Cursor hooks installed in {cursor_hooks}"
                + (f" (backup: {backup})" if backup else "")
            )
    except installer.InstallError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@hooks_app.command("uninstall")
def hooks_uninstall(
    claude_settings: Annotated[
        Path, typer.Option(help="Claude Code settings file.")
    ] = DEFAULT_CLAUDE_SETTINGS,
    cursor_hooks: Annotated[Path, typer.Option(help="Cursor hooks file.")] = DEFAULT_CURSOR_HOOKS,
) -> None:
    """Remove only WorkDigest's hook entries."""
    try:
        for label, path, remove in (
            ("Claude Code", claude_settings, installer.uninstall_claude),
            ("Cursor", cursor_hooks, installer.uninstall_cursor),
        ):
            backup = remove(path)
            console.print(
                f"{label}: " + (f"removed (backup: {backup})" if backup else "nothing to remove")
            )
    except installer.InstallError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@hooks_app.command("status")
def hooks_status(
    claude_settings: Annotated[
        Path, typer.Option(help="Claude Code settings file.")
    ] = DEFAULT_CLAUDE_SETTINGS,
    cursor_hooks: Annotated[Path, typer.Option(help="Cursor hooks file.")] = DEFAULT_CURSOR_HOOKS,
) -> None:
    """Show which events route to WorkDigest."""
    for label, status in (
        ("Claude Code", installer.claude_status(claude_settings)),
        ("Cursor", installer.cursor_status(cursor_hooks)),
    ):
        state = (
            "complete"
            if status.complete
            else f"{len(status.installed_events)}/{len(status.expected_events)}"
        )
        console.print(f"{label} ({status.path}): {state}")


# ------------------------------------------------------------------ secrets
def _secret_name(name: str) -> str:
    aliases = {
        "jira": "jira-token",
        "bitbucket": "bitbucket-token",
        "supabase-db": "supabase-db-password",
    }
    key = aliases.get(name, name)
    if key not in KNOWN_SECRETS:
        console.print(f"[red]Unknown secret {name!r}; use one of: {', '.join(aliases)}[/red]")
        raise typer.Exit(2)
    return key


@secrets_app.command("set")
def secrets_set(
    name: Annotated[str, typer.Argument(help="jira, bitbucket or supabase-db")],
) -> None:
    """Store a token in the Keychain (input is hidden and never echoed or logged)."""
    key = _secret_name(name)
    value = typer.prompt(f"Paste the {name} secret", hide_input=True)
    try:
        SecretStore().set(key, value)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"Stored {key} in the Keychain (service {SECRET_SERVICE}).")


@secrets_app.command("status")
def secrets_status() -> None:
    """Show which tokens are stored (never their values)."""
    store = SecretStore()
    for key in KNOWN_SECRETS:
        console.print(f"{key}: {'stored' if store.get(key) else 'missing'}")


@secrets_app.command("delete")
def secrets_delete(
    name: Annotated[str, typer.Argument(help="jira, bitbucket or supabase-db")],
) -> None:
    """Remove a token from the Keychain."""
    key = _secret_name(name)
    console.print(f"{key}: {'deleted' if SecretStore().delete(key) else 'was not stored'}")


# ------------------------------------------------------------------- ingest
@app.command()
def ingest(
    stage: Annotated[
        list[str] | None, typer.Option(help=f"Stages to run: {', '.join(STAGES)}.")
    ] = None,
) -> None:
    """Run one ingest pass (spool, transcripts, git)."""
    paths = _paths()
    config = _config(paths)
    store = _store(paths)
    try:
        selected = tuple(stage) if stage else STAGES
        unknown = set(selected) - set(STAGES)
        if unknown:
            console.print(f"[red]Unknown stage(s): {', '.join(sorted(unknown))}[/red]")
            raise typer.Exit(2)
        report = run_pass(store, config, paths, selected)
        for name, result in report.stages.items():
            console.print(f"{name}: {result}")
        for name, failure in report.failures.items():
            console.print(f"[red]{name} failed:[/red] {failure}")
    finally:
        store.close()


@app.command()
def run(
    interval: Annotated[int, typer.Option(help="Seconds between spool ingests.")] = 60,
    once: Annotated[bool, typer.Option(help="Run a single full pass and exit.")] = False,
) -> None:
    """Run the recorder loop (launchd runs this in the background)."""
    paths = _paths()
    config = _config(paths)
    last_git = last_transcripts = 0.0
    while True:
        store = _store(paths)
        try:
            now = time.monotonic()
            stages = ["spool"]
            if once or now - last_transcripts >= TRANSCRIPT_EVERY_S:
                stages += ["claude_transcripts", "cursor_transcripts"]
                last_transcripts = now
            if once or now - last_git >= GIT_POLL_EVERY_S:
                stages.append("git")
                last_git = now
            run_pass(store, config, paths, tuple(stages))
        finally:
            store.close()
        if once:
            return
        time.sleep(interval)


# -------------------------------------------------------------------- today
@app.command()
def today(
    day: Annotated[
        str | None, typer.Option("--date", help="Work day YYYY-MM-DD (default: today).")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")] = False,
    refresh: Annotated[bool, typer.Option(help="Run an ingest pass first.")] = True,
) -> None:
    """Print the evidence captured for a work day."""
    paths = _paths()
    config = _config(paths)
    store = _store(paths)
    try:
        if refresh:
            run_pass(store, config, paths)
        target = day or work_date(now_local(), config.day.boundary_hour).isoformat()
        try:
            date.fromisoformat(target)
        except ValueError as exc:
            console.print(f"[red]Invalid --date {target!r}; expected YYYY-MM-DD[/red]")
            raise typer.Exit(2) from exc
        evidence = day_evidence(store, target, now_local(), config.day.idle_timeout_minutes)
    finally:
        store.close()
    if as_json:
        typer.echo(json.dumps(evidence.model_dump(), indent=2, ensure_ascii=False))
        return
    _render(evidence)


def _render(evidence: DayEvidence) -> None:
    console.rule(f"Evidence for {evidence.work_date}")
    if not (evidence.sessions or evidence.commits or evidence.test_runs):
        console.print("Nothing captured yet. Check `digest hooks status` and `digest doctor`.")
    if evidence.sessions:
        table = Table(title="Agent sessions", show_lines=False)
        for column in (
            "Source",
            "Session",
            "Title",
            "Repos",
            "Time",
            "Status",
            "Prompts",
            "Tools",
            "Edits",
            "Tickets",
        ):
            table.add_column(column)
        for s in evidence.sessions:
            table.add_row(
                s.source,
                s.session_key.split(":", 1)[1][:8],
                (s.title or "")[:40],
                ", ".join(s.repos[:3]),
                f"{s.first_ts[11:16]}-{s.last_ts[11:16]}",
                s.status,
                str(s.prompts),
                str(s.tool_uses + s.shell_runs),
                str(s.file_edits),
                ", ".join(s.tickets),
            )
        console.print(table)
    if evidence.commits:
        table = Table(title="Commits (mine)")
        for column in ("Repo", "SHA", "Subject", "Branch", "Pushed", "+/-", "Tickets"):
            table.add_column(column)
        for c in evidence.commits:
            table.add_row(
                c.repo,
                c.sha[:8],
                c.subject[:60],
                ", ".join(c.branches[:2]),
                "yes" if c.pushed else "NO",
                f"+{c.insertions}/-{c.deletions}",
                ", ".join(c.tickets),
            )
        console.print(table)
    if evidence.test_runs:
        table = Table(title="Test runs")
        for column in ("Time", "Repo", "Runner", "Result", "Summary"):
            table.add_column(column)
        for t in evidence.test_runs:
            result = f"{t.passed}/{t.total} {t.unit}" + (f", {t.failed} failed" if t.failed else "")
            table.add_row(t.ts[11:16], t.repo or "-", t.runner, result, t.summary_line[:70])
        console.print(table)
    for b in evidence.branches_created:
        console.print(f"New branch: {b['repo']} {b['branch']} ({b['ts'][:16]})")
    console.print(f"Tickets: {', '.join(evidence.tickets) or 'none detected'}")
    console.print(
        f"Events by source: {evidence.counts}  ·  ingest errors today: {evidence.ingest_errors}"
    )


# ------------------------------------------------------------------- doctor
@app.command()
def doctor(
    claude_settings: Annotated[
        Path, typer.Option(help="Claude Code settings file.")
    ] = DEFAULT_CLAUDE_SETTINGS,
    cursor_hooks: Annotated[Path, typer.Option(help="Cursor hooks file.")] = DEFAULT_CURSOR_HOOKS,
) -> None:
    """Check configuration, hooks, repos and recent ingest errors."""
    paths = _paths()
    problems = 0
    console.print(f"Home: {paths.home}")
    try:
        config = load_config(paths.config_path)
        console.print(
            f"Config: {'ok' if paths.config_path.exists() else 'missing (defaults in use)'}"
        )
    except ConfigError as exc:
        console.print(f"[red]Config: {exc}[/red]")
        raise typer.Exit(1) from exc
    if not config.identity.author_emails:
        problems += 1
        console.print(
            "[yellow]identity.author_emails is empty: commits cannot be attributed[/yellow]"
        )
    for repo in config.repos:
        ok = (repo.path / ".git").exists()
        problems += int(not ok)
        console.print(
            f"Repo {repo.display_name}: {'ok' if ok else '[red]not a git repo[/red]'} ({repo.path})"
        )
    if not config.repos:
        console.print("[yellow]No \\[\\[repos]] configured: git evidence will be empty[/yellow]")
    for label, status in (
        ("Claude Code hooks", installer.claude_status(claude_settings)),
        ("Cursor hooks", installer.cursor_status(cursor_hooks)),
    ):
        problems += int(not status.complete)
        console.print(f"{label}: {'ok' if status.complete else 'not installed'}")
    service = LaunchAgent(paths).status()
    console.print(
        f"Background daemon: {f'running (pid {service.pid})' if service.pid else '[yellow]not running[/yellow] (digest service start)'}"
    )
    if paths.db_path.exists():
        store = Store.open(paths.db_path)
        try:
            for row in store.query(
                "SELECT ts, stage, error FROM ingest_errors ORDER BY id DESC LIMIT 5"
            ):
                console.print(f"[dim]{row['ts'][:19]} {row['stage']}: {row['error'][:120]}[/dim]")
        finally:
            store.close()
    raise typer.Exit(1 if problems else 0)


digest_cli.register(app)
app.add_typer(cloud_cli.cloud_app, name="cloud")
service_cli.register(app)
setup_cli.register(app)


if __name__ == "__main__":
    app()
