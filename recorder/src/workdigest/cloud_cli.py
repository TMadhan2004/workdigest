"""``digest cloud …`` commands: Supabase setup, sign-in, push/pull and the job worker."""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape

from workdigest.cloud.client import CloudAuth, CloudError, Rest
from workdigest.cloud.sync import plan_push, pull, push
from workdigest.cloud.worker import tick
from workdigest.config import Config, ConfigError, load_config
from workdigest.digest.service import DigestContext, add_reply
from workdigest.integrations.http import HttpError
from workdigest.paths import Paths
from workdigest.store.db import Store

console = Console()
cloud_app = typer.Typer(
    help="Supabase sync (redacted summaries only; see `cloud push --dry-run`).",
    no_args_is_help=True,
)


def _config(paths: Paths) -> Config:
    try:
        return load_config(paths.config_path)
    except ConfigError as exc:
        console.print(f"[red]Config error:[/red] {escape(str(exc))}")
        raise typer.Exit(2) from exc


def _rest(config: Config) -> Rest:
    try:
        auth = CloudAuth(config.cloud.url, config.cloud.publishable_key)
        rest = Rest(auth, config.cloud.publishable_key)
        _ = rest.user_id  # fail fast when not signed in
    except (CloudError, HttpError) as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    return rest


def _set_cloud_config(paths: Paths, values: dict[str, object]) -> None:
    """Merge values into the [cloud] table of config.toml (keeps the rest of the file)."""
    text = paths.config_path.read_text(encoding="utf-8") if paths.config_path.exists() else ""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == "[cloud]"), None)
    rendered = {
        k: json.dumps(v) if not isinstance(v, bool) else str(v).lower() for k, v in values.items()
    }
    if start is None:
        lines += ["", "[cloud]", *(f"{k} = {v}" for k, v in rendered.items())]
    else:
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].startswith("[")), len(lines)
        )
        block = {
            line.split("=", 1)[0].strip(): line for line in lines[start + 1 : end] if "=" in line
        }
        for key, value in rendered.items():
            block[key] = f"{key} = {value}"
        lines[start + 1 : end] = list(block.values())
    paths.config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@cloud_app.command("setup")
def setup(
    project_ref: Annotated[str, typer.Argument(help="Supabase project ref, e.g. your-project-ref")],
) -> None:
    """Store the project URL and its public publishable key (fetched with the logged-in Supabase CLI)."""
    paths = Paths.resolve()
    completed = subprocess.run(
        ["supabase", "projects", "api-keys", "--project-ref", project_ref, "-o", "json"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        console.print(f"[red]supabase CLI failed: {escape(completed.stderr.strip()[:300])}[/red]")
        raise typer.Exit(1)
    keys = json.loads(completed.stdout)
    # Only the public key is kept. The service-role/secret key is never read into a variable.
    public = next(
        (
            k.get("api_key")
            for k in keys
            if k.get("type") == "publishable" or k.get("name") in {"anon", "publishable"}
        ),
        None,
    )
    if not public:
        console.print("[red]No publishable/anon key found for this project[/red]")
        raise typer.Exit(1)
    config = _config(paths)
    _set_cloud_config(
        paths,
        {
            "enabled": True,
            "url": f"https://{project_ref}.supabase.co",
            "publishable_key": public,
            "device_id": config.cloud.device_id or f"mac-{uuid.uuid4().hex[:12]}",
        },
    )
    console.print(
        f"Cloud configured for https://{project_ref}.supabase.co (publishable key stored in config.toml)."
    )


@cloud_app.command("signup")
def signup(email: Annotated[str, typer.Option(help="Account email")]) -> None:
    """Create your account (one-time). Supabase may email you a confirmation link."""
    config = _config(Paths.resolve())
    password = typer.prompt("Choose a password", hide_input=True, confirmation_prompt=True)
    result = CloudAuth(config.cloud.url, config.cloud.publishable_key).sign_up(email, password)
    if result.get("access_token"):
        console.print("Account created and signed in.")
    else:
        console.print(
            "Account created. Confirm the email Supabase sent, then run `digest cloud login`."
        )


@cloud_app.command("login")
def login(email: Annotated[str | None, typer.Option(help="Account email")] = None) -> None:
    """Sign in; only the refresh token is stored, in the Keychain."""
    paths = Paths.resolve()
    config = _config(paths)
    address = email or config.cloud.email or typer.prompt("Email")
    password = typer.prompt("Password", hide_input=True)
    try:
        session = CloudAuth(config.cloud.url, config.cloud.publishable_key).sign_in(
            address, password
        )
    except (CloudError, HttpError) as exc:
        console.print(f"[red]Sign-in failed: {escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    _set_cloud_config(paths, {"email": address})
    console.print(f"Signed in as {escape(session.email or address)}.")


@cloud_app.command("logout")
def logout() -> None:
    """Forget the stored session."""
    config = _config(Paths.resolve())
    CloudAuth(config.cloud.url, config.cloud.publishable_key).sign_out()
    console.print("Signed out.")


@cloud_app.command("push")
def push_cmd(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would be uploaded, send nothing.")
    ] = False,
) -> None:
    """Upload changed rows (redacted summaries, drafts, asks, Jira/PR metadata)."""
    paths = Paths.resolve()
    config = _config(paths)
    store = Store.open(paths.db_path)
    try:
        if dry_run:
            plan = plan_push(store, config)
            for table, rows in plan.changes.items():
                for key, _, row in rows[:3]:
                    preview = json.dumps(row, ensure_ascii=False)[:240]
                    console.print(f"[dim]{table}[/dim] {escape(key)}: {escape(preview)}")
                if len(rows) > 3:
                    console.print(f"[dim]{table}: … {len(rows) - 3} more[/dim]")
            console.print(f"Would upload {plan.total} rows: {plan.counts()}")
            return
        if not config.cloud.enabled:
            console.print("[yellow]cloud.enabled is false; run `digest cloud setup` first[/yellow]")
            raise typer.Exit(2)
        counts = push(store, config, _rest(config))
    finally:
        store.close()
    console.print(f"Uploaded: {counts or 'nothing changed'}")


@cloud_app.command("pull")
def pull_cmd() -> None:
    """Apply changes made in the app (asks, reply items, edited digests, pasted replies)."""
    paths = Paths.resolve()
    config = _config(paths)
    store = Store.open(paths.db_path)
    try:
        rest = _rest(config)

        def on_new_reply(reply_id: str, text: str, for_date: str | None) -> None:
            from datetime import date

            add_reply(
                store,
                config,
                text,
                date.fromisoformat(for_date) if for_date else None,
                None,
                confirm=False,
                reply_id=reply_id,
            )

        report = pull(store, config, rest, on_new_reply)
    finally:
        store.close()
    console.print(
        f"Pulled: {report.asks} asks, {report.reply_items} reply items ({report.replies_confirmed} replies confirmed), "
        f"{len(report.new_replies)} new replies, {report.edited_digests} edited digests."
    )


@cloud_app.command("worker")
def worker(
    once: Annotated[bool, typer.Option(help="Run one iteration and exit.")] = False,
    interval: Annotated[int, typer.Option(help="Seconds between iterations.")] = 30,
) -> None:
    """Heartbeat, pull, run queued jobs from the app or the 20:00 schedule, push."""
    paths = Paths.resolve()
    config = _config(paths)
    if not config.cloud.enabled or not config.cloud.device_id:
        console.print("[yellow]Run `digest cloud setup` and `digest cloud login` first[/yellow]")
        raise typer.Exit(2)
    rest = _rest(config)
    while True:
        store = Store.open(paths.db_path)
        try:
            report = tick(DigestContext(store, config, paths), rest, config.cloud.device_id)
            if report.jobs_done or report.jobs_failed or report.pushed:
                console.print(
                    f"jobs done {len(report.jobs_done)}, failed {len(report.jobs_failed)}; pushed {report.pushed or '-'}"
                )
        except (CloudError, HttpError) as exc:
            console.print(f"[yellow]cloud: {escape(str(exc))}[/yellow]")
        finally:
            store.close()
        if once:
            return
        time.sleep(interval)
