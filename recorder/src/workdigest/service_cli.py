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
    help="Start, stop or check the background service (launchd: starts at login).",
    no_args_is_help=True,
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


def start(
    executable: Annotated[
        Path | None, typer.Option(help="The `digest` executable launchd should run.")
    ] = None,
) -> None:
    """Start the background service now and at every login (restarts it if it is running)."""
    agent = LaunchAgent(Paths.resolve())
    try:
        plist = agent.install(executable or default_executable())
    except (FileNotFoundError, RuntimeError) as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"Started (installed {plist}); logs in {agent.paths.log_dir}")


def stop() -> None:
    """Stop the background service; it stays stopped, also after a restart, until `digest service start`."""
    removed = LaunchAgent(Paths.resolve()).uninstall()
    console.print(
        "Stopped (removed the launchd agent). Run `digest service start` to start it again."
        if removed
        else "The service was not running"
    )


@service_app.command()
def restart(
    executable: Annotated[
        Path | None, typer.Option(help="The `digest` executable launchd should run.")
    ] = None,
) -> None:
    """Restart the background service (settings in config.toml apply without a restart)."""
    start(executable)


# start/stop are the names people use; install/uninstall stay as hidden aliases for scripts.
service_app.command("start")(start)
service_app.command("stop")(stop)
service_app.command("install", hidden=True)(start)
service_app.command("uninstall", hidden=True)(stop)


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
