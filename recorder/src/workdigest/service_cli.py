"""`digest daemon` and `digest service …`: run the background daemon and manage its launchd agent."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape

from workdigest.config import ConfigError, load_config
from workdigest.daemon import build_daemon, setup_logging
from workdigest.paths import Paths
from workdigest.service import LaunchAgent

service_app = typer.Typer(
    help="Start the daemon automatically at login (launchd).", no_args_is_help=True
)
console = Console()


def default_executable() -> Path:
    """The `digest` entry point of the running environment (the project venv)."""
    return Path(sys.executable).parent / "digest"


def daemon(
    once: Annotated[bool, typer.Option(help="Run every task once and exit (for testing).")] = False,
) -> None:
    """Run the background daemon: ingest, git, cloud worker, summaries, nudges (launchd runs this)."""
    paths = Paths.resolve()
    try:
        config = load_config(paths.config_path)
    except ConfigError as exc:
        console.print(f"[red]Config error:[/red] {escape(str(exc))}")
        raise typer.Exit(2) from exc
    setup_logging(paths)
    runner = build_daemon(paths, config)
    if once:
        console.print(f"ran: {', '.join(runner.tick()) or '-'}")
        return
    runner.run_forever()


@service_app.command()
def install(
    executable: Annotated[
        Path | None, typer.Option(help="The `digest` executable launchd should run.")
    ] = None,
) -> None:
    """Install and start the launchd agent (per-user; `digest service uninstall` removes it)."""
    agent = LaunchAgent(Paths.resolve())
    try:
        plist = agent.install(executable or default_executable())
    except (FileNotFoundError, RuntimeError) as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"Installed {plist}; logs in {agent.paths.log_dir}")


@service_app.command()
def uninstall() -> None:
    """Stop the daemon and remove the launchd agent."""
    removed = LaunchAgent(Paths.resolve()).uninstall()
    console.print("Removed the launchd agent" if removed else "The launchd agent was not installed")


@service_app.command()
def status() -> None:
    """Show whether the agent is installed, loaded and running."""
    state = LaunchAgent(Paths.resolve()).status()
    running = f"running (pid {state.pid})" if state.pid else "not running"
    console.print(
        f"Installed: {'yes' if state.installed else 'no'}; loaded: {'yes' if state.loaded else 'no'}; {running}"
    )
    if state.last_exit and state.last_exit not in {"0", "(never exited)"}:
        console.print(f"[yellow]Last exit: {escape(state.last_exit)}[/yellow]")
    raise typer.Exit(0 if state.pid else 1)


def register(app: typer.Typer) -> None:
    """Attach the commands to the main CLI."""
    app.command("daemon")(daemon)
    app.add_typer(service_app, name="service")
