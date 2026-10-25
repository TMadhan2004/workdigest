"""``digest setup``, ``digest update`` and ``digest uninstall``."""

from __future__ import annotations

import shutil
import subprocess
import webbrowser
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape

from workdigest.config import ConfigError, load_config
from workdigest.hooks import installer
from workdigest.paths import Paths
from workdigest.secrets import KNOWN_SECRETS, SecretStore
from workdigest.service import LaunchAgent
from workdigest.setup.api import CLAUDE_SETTINGS, CURSOR_HOOKS, SetupApi
from workdigest.setup.server import SetupServer, web_root
from workdigest.setup.team import load_team
from workdigest.update_check import latest_release, version_tuple
from workdigest.version import __version__

console = Console()


def setup(
    browser: Annotated[bool, typer.Option(help="Open the page in your browser.")] = True,
    port: Annotated[int, typer.Option(help="Local port (0 = any free port).")] = 0,
) -> None:
    """Open the setup page: account, manager, repos, Jira/Bitbucket, AI, service (re-run to change anything)."""
    paths = Paths.resolve()
    paths.ensure()
    if not (web_root() / "index.html").is_file():
        console.print(
            "[red]The setup pages are missing from this install. Run `make web` (developers) or reinstall.[/red]"
        )
        raise typer.Exit(1)
    server = SetupServer(SetupApi(paths), port=port)
    if browser:
        console.print(
            "WorkDigest setup is open in your browser. Keep this window open until you finish."
        )
        console.print(
            f"If the page did not open, paste this link into your browser:\n  {server.url}"
        )
    else:
        console.print(f"Open this link in your browser:\n  {server.url}")
    console.print("[dim]The page only runs on this Mac (127.0.0.1). Press Ctrl+C to stop.[/dim]")
    if browser:
        webbrowser.open(server.url)
    try:
        server.serve()
    except KeyboardInterrupt:
        console.print("\nSetup stopped. Run `digest setup` again to continue where you left off.")
        return
    running = LaunchAgent(paths).status().pid is not None
    console.print(
        "Setup finished. WorkDigest now runs in the background."
        if running
        else "Setup closed. The background service is not running yet; run `digest setup` to finish."
    )


# ----------------------------------------------------------------- update
def update(
    check: Annotated[
        bool, typer.Option("--check", help="Only say whether an update exists.")
    ] = False,
) -> None:
    """Install the newest WorkDigest for the Mac and restart the background service."""
    paths = Paths.resolve()
    try:
        team = load_team(load_config(paths.config_path))
    except ConfigError as exc:
        console.print(f"[red]Config error:[/red] {escape(str(exc))}")
        raise typer.Exit(2) from exc
    if not team.site_url:
        console.print(
            "[red]This build does not know the WorkDigest website; reinstall from it.[/red]"
        )
        raise typer.Exit(2)
    try:
        latest = latest_release(team.site_url)
    except OSError as exc:
        console.print(f"[red]Could not reach {escape(team.site_url)}: {escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    version = str(latest.get("version", "0"))
    if version_tuple(version) <= version_tuple(__version__):
        console.print(f"WorkDigest {__version__} is the newest version.")
        return
    console.print(f"WorkDigest {version} is available (you have {__version__}).")
    if check:
        raise typer.Exit(10)
    uv = shutil.which("uv") or str(Path("~/.local/bin/uv").expanduser())
    wheel = str(latest.get("mac", {}).get("wheel_url", ""))
    if not wheel:
        console.print("[red]The release has no Mac package.[/red]")
        raise typer.Exit(1)
    completed = subprocess.run(
        [uv, "tool", "install", "--force", "--python", "3.12", wheel], check=False
    )
    if completed.returncode != 0:
        console.print("[red]The update failed; your current version still works.[/red]")
        raise typer.Exit(1)
    if LaunchAgent(paths).status().installed:
        digest = shutil.which("digest") or str(Path("~/.local/bin/digest").expanduser())
        subprocess.run([digest, "service", "start"], check=False)  # the new code restarts it
    console.print(f"Updated to {version}.")


# -------------------------------------------------------------- uninstall
def uninstall(
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask before each step.")] = False,
    delete_data: Annotated[bool, typer.Option(help="Also delete the local data folder.")] = False,
) -> None:
    """Stop the service, remove the agent hooks and the Keychain items (data is kept unless asked)."""
    paths = Paths.resolve()
    if not yes and not typer.confirm(
        "Stop WorkDigest and remove its service, hooks and Keychain items?"
    ):
        raise typer.Exit(1)
    console.print(
        "Service: " + ("removed" if LaunchAgent(paths).uninstall() else "was not installed")
    )
    for label, path, remove in (
        ("Claude Code hooks", CLAUDE_SETTINGS, installer.uninstall_claude),
        ("Cursor hooks", CURSOR_HOOKS, installer.uninstall_cursor),
    ):
        try:
            backup = remove(path)
            console.print(f"{label}: " + (f"removed (backup: {backup})" if backup else "none"))
        except installer.InstallError as exc:
            console.print(f"[yellow]{label}: {escape(str(exc))}[/yellow]")
    store = SecretStore()
    removed = [name for name in KNOWN_SECRETS if store.delete(name)]
    console.print(f"Keychain: removed {len(removed)} item(s)")
    if delete_data or (
        not yes and typer.confirm(f"Also delete your local data in {paths.home}?", default=False)
    ):
        shutil.rmtree(paths.home, ignore_errors=True)
        console.print("Local data deleted.")
    else:
        console.print(f"Local data kept in {paths.home}")
    console.print("Last step: run `uv tool uninstall workdigest` to remove the program.")


def register(app: typer.Typer) -> None:
    """Attach the commands to the main CLI."""
    app.command("setup")(setup)
    app.command("update")(update)
    app.command("uninstall")(uninstall)
