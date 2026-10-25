"""The setup wizard's API: one small action per step, each safe to repeat.

The browser page (the Angular ``setup`` app) calls these through ``server.py``. Secrets go to
the Keychain and are never returned; the account password is only sent to Supabase and is
kept in memory until the sign-in succeeds (for "I've confirmed my email").
"""

from __future__ import annotations

import subprocess
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from workdigest import config_write
from workdigest.clock import now_local
from workdigest.cloud.client import CloudAuth, CloudError, Rest
from workdigest.config import Config, ConfigError, load_config
from workdigest.digest import onboarding
from workdigest.hooks import installer
from workdigest.integrations.http import HttpError
from workdigest.integrations.sync import Clients, make_clients
from workdigest.llm import health
from workdigest.paths import Paths
from workdigest.secrets import SecretStore
from workdigest.service import LaunchAgent
from workdigest.setup.team import Team, load_team
from workdigest.store.db import Store

CLAUDE_SETTINGS = Path("~/.claude/settings.json").expanduser()
CURSOR_HOOKS = Path("~/.cursor/hooks.json").expanduser()
SKIP_DIRS = {"node_modules", "Library", "Applications", "Pictures", "Music", "Movies", "venv"}
MAX_REPOS = 200
HEARTBEAT_FRESH = timedelta(minutes=2)
MIN_PASSWORD = 8
MAX_UPLOAD = 2_000_000
LOGIN_COMMANDS = {"cursor_cli": "cursor-agent login", "claude_cli": "claude  (then type /login)"}


class SetupError(ValueError):
    """A step could not be completed; the message is shown to the user as is."""


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], capture_output=True, text=True, timeout=10, check=False
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def find_repos(root: Path, depth: int = 3) -> list[Path]:
    """Git repositories under ``root`` (not inside other repos, skipping hidden and bulky folders)."""
    found: list[Path] = []

    def walk(folder: Path, level: int) -> None:
        if len(found) >= MAX_REPOS:
            return
        if (folder / ".git").exists():
            found.append(folder)
            return
        if level >= depth:
            return
        try:
            children = sorted(p for p in folder.iterdir() if p.is_dir())
        except OSError:
            return
        for child in children:
            if not child.name.startswith(".") and child.name not in SKIP_DIRS:
                walk(child, level + 1)

    walk(root.expanduser(), 0)
    return found


@dataclass
class Deps:
    """What the API talks to; tests replace these."""

    secrets: SecretStore = field(default_factory=SecretStore)
    auth: Callable[[Team], CloudAuth] | None = None
    git: Callable[..., str] = _git
    prober: Callable[[str], health.Capability] = health.probe
    clients: Callable[[Config, SecretStore], Clients] = make_clients
    agent: Callable[[Paths], LaunchAgent] = LaunchAgent
    executable: Callable[[], Path] | None = None
    claude_settings: Path = CLAUDE_SETTINGS
    cursor_hooks: Path = CURSOR_HOOKS
    hook_python: Callable[[], str] = installer.default_python
    clock: Callable[[], datetime] = now_local


class SetupApi:
    """State and actions for one run of ``digest setup``."""

    def __init__(self, paths: Paths, deps: Deps | None = None, team: Team | None = None) -> None:
        """Bind to one WorkDigest home."""
        self.paths = paths
        self.deps = deps or Deps()
        self.team = team or load_team(self.config)
        self._pending: tuple[str, str] | None = None  # (email, password) until sign-in succeeds
        self.finished = False

    # ---------------------------------------------------------------- helpers
    @property
    def config(self) -> Config:
        """The current config (defaults when there is none yet)."""
        try:
            return load_config(self.paths.config_path)
        except ConfigError as exc:
            raise SetupError(f"config.toml is invalid: {exc}") from exc

    def _write(self, tables: dict[str, dict[str, Any]] | None = None, **top: Any) -> Config:
        try:
            return config_write.update(self.paths.config_path, tables, **top)
        except ValueError as exc:
            raise SetupError(f"that value is not valid: {exc}") from exc

    def _auth(self) -> CloudAuth:
        if not self.team.cloud_url or not self.team.publishable_key:
            raise SetupError("this build has no team cloud settings; reinstall from the website")
        if self.deps.auth:
            return self.deps.auth(self.team)
        return CloudAuth(
            self.team.cloud_url,
            self.team.publishable_key,
            secrets=self.deps.secrets,
            lock_path=self.paths.home / "auth.lock",
        )

    def _signed_in(self) -> bool:
        return bool(self.deps.secrets.get("supabase-refresh-token"))

    # ------------------------------------------------------------------ state
    def state(self) -> dict[str, Any]:
        """Everything already set, so each step can show it (no secret values)."""
        config = self.config
        secrets = self.deps.secrets
        status = self.deps.agent(self.paths).status()
        return {
            "team": {
                "domain": self.team.domain,
                "site_url": self.team.site_url,
                "apk_url": self.team.apk_url,
            },
            "account": {"email": config.cloud.email, "signed_in": self._signed_in()},
            "profile": {
                "name": config.identity.name or self.deps.git("config", "--global", "user.name"),
                "emails": config.identity.author_emails
                or [e for e in [self.deps.git("config", "--global", "user.email")] if e],
                "manager_name": config.digest.manager_name,
                "team_members": config.digest.team_members,
            },
            "conversation": {"learned": (self.paths.home / "template.json").exists()},
            "repos": [str(r.path) for r in config.repos],
            "atlassian": {
                "email": config.atlassian_email or config.cloud.email,
                "jira_token": bool(secrets.get("jira-token")),
                "bitbucket_token": bool(secrets.get("bitbucket-token")),
                "jira_site": self.team.jira_site,
                "bitbucket_workspace": self.team.bitbucket_workspace,
            },
            "ai": {"provider": config.digest.provider},
            "hooks": {
                "claude": installer.claude_status(self.deps.claude_settings).complete,
                "cursor": installer.cursor_status(self.deps.cursor_hooks).complete,
            },
            "service": {"installed": status.installed, "running": bool(status.pid)},
        }

    # ---------------------------------------------------------------- account
    def account(self, body: dict[str, Any]) -> dict[str, Any]:
        """Sign up or sign in with a company email."""
        email = str(body.get("email", "")).strip().lower()
        password = str(body.get("password", ""))
        mode = body.get("mode", "signup")
        if not self.team.allows(email):
            raise SetupError(f"use your @{self.team.domain} email address")
        if mode == "signup" and len(password) < MIN_PASSWORD:
            raise SetupError(f"choose a password of at least {MIN_PASSWORD} characters")
        auth = self._auth()
        try:
            if mode == "signup":
                result = auth.sign_up(email, password)
                if not result.get("access_token"):
                    self._pending = (email, password)
                    self._save_account(email)
                    return {"status": "confirm_email", "email": email}
            else:
                auth.sign_in(email, password)
        except (CloudError, HttpError) as exc:
            raise SetupError(_auth_message(exc, mode)) from exc
        self._pending = None
        self._save_account(email)
        return {"status": "signed_in", "email": email}

    def confirmed(self, _body: dict[str, Any]) -> dict[str, Any]:
        """After clicking the email link: sign in with the details given a moment ago."""
        if not self._pending:
            raise SetupError("sign in with your email and password")
        email, password = self._pending
        try:
            self._auth().sign_in(email, password)
        except (CloudError, HttpError) as exc:
            raise SetupError(
                "not confirmed yet: open the link in the email from Supabase, then try again"
            ) from exc
        self._pending = None
        return {"status": "signed_in", "email": email}

    def _save_account(self, email: str) -> None:
        config = self.config
        self._write(
            {
                "cloud": {
                    "url": self.team.cloud_url,
                    "publishable_key": self.team.publishable_key,
                    "email": email,
                    "device_id": config.cloud.device_id or f"mac-{uuid.uuid4().hex[:12]}",
                },
                "atlassian": {
                    "email": config.atlassian.email or email,
                    "jira_site": self.team.jira_site,
                    "bitbucket_workspace": self.team.bitbucket_workspace,
                },
            }
        )

    # ---------------------------------------------------------------- profile
    def profile(self, body: dict[str, Any]) -> dict[str, Any]:
        """Your name, git emails, manager and usual teammates."""
        name = str(body.get("name", "")).strip()
        emails = [str(e).strip() for e in body.get("emails", []) if str(e).strip()]
        manager = str(body.get("manager_name", "")).strip()
        if not name or not emails or not manager:
            raise SetupError("enter your name, at least one git email and your manager's name")
        team = [str(t).strip() for t in body.get("team_members", []) if str(t).strip()]
        self._write(
            {
                "identity": {"name": name, "author_emails": emails},
                "digest": {"manager_name": manager, "team_members": team},
            }
        )
        return {"saved": True}

    # ----------------------------------------------------------- conversation
    def conversation_preview(self, body: dict[str, Any]) -> dict[str, Any]:
        """What we learned from the uploaded file (nothing is saved yet)."""
        return self._learn(body).preview()

    def conversation_apply(self, body: dict[str, Any]) -> dict[str, Any]:
        """Save the learned format and import the digests, rules and asks."""
        learned = self._learn(body)
        manager = str(body.get("manager_name", "")).strip() or learned.template.manager_name
        if manager and manager != learned.template.manager_name:
            learned.template = learned.template.model_copy(update={"manager_name": manager})
        self.paths.ensure()
        store = Store.open(self.paths.db_path)
        try:
            counts = onboarding.apply(self.paths, store, self.config, str(body["text"]), learned)
        finally:
            store.close()
        if manager and not self.config.digest.manager_name:
            self._write({"digest": {"manager_name": manager}})
        return counts

    @staticmethod
    def _learn(body: dict[str, Any]) -> onboarding.Learned:
        text = str(body.get("text", ""))
        if len(text) > MAX_UPLOAD:
            raise SetupError("that file is too large (2 MB at most)")
        try:
            return onboarding.learn(text)
        except onboarding.OnboardingError as exc:
            raise SetupError(str(exc)) from exc

    # ------------------------------------------------------------------ repos
    def repos_scan(self, body: dict[str, Any]) -> dict[str, Any]:
        """Git repositories under a folder (default: your home folder)."""
        root = Path(str(body.get("root") or "~")).expanduser()
        if not root.is_dir():
            raise SetupError(f"{root} is not a folder")
        found = find_repos(root)
        return {
            "root": str(root),
            "repos": [str(p) for p in found],
            "capped": len(found) >= MAX_REPOS,
        }

    def repos(self, body: dict[str, Any]) -> dict[str, Any]:
        """The repositories whose commits count as your work."""
        paths = [Path(str(p)).expanduser() for p in body.get("paths", [])]
        missing = [str(p) for p in paths if not (p / ".git").exists()]
        if missing:
            raise SetupError("not git repositories: " + ", ".join(missing))
        self._write(repos=[{"path": str(p)} for p in paths])
        return {"saved": len(paths)}

    # -------------------------------------------------------------- atlassian
    def atlassian(self, body: dict[str, Any]) -> dict[str, Any]:
        """Store the tokens given (Keychain only) and check that both work."""
        email = str(body.get("email", "")).strip()
        if email:
            self._write({"atlassian": {"email": email}})
        for name in ("jira", "bitbucket"):
            token = str(body.get(f"{name}_token") or "").strip()
            if token:
                self.deps.secrets.set(f"{name}-token", token)
        return self.atlassian_check()

    def atlassian_check(self, _body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Who each token belongs to, or why it fails."""
        clients = self.deps.clients(self.config, self.deps.secrets)
        result: dict[str, Any] = {}
        if clients.jira is None:
            result["jira"] = {"ok": False, "detail": "no token yet"}
        else:
            try:
                result["jira"] = {
                    "ok": True,
                    "detail": str(clients.jira.myself().get("displayName")),
                }
            except HttpError as exc:
                result["jira"] = {"ok": False, "detail": _http_detail(exc)}
        if clients.bitbucket is None:
            result["bitbucket"] = {"ok": False, "detail": "no token yet"}
        else:
            try:
                user = clients.bitbucket.user()
                result["bitbucket"] = {"ok": True, "detail": str(user.get("display_name"))}
            except HttpError as exc:
                result["bitbucket"] = {"ok": False, "detail": _http_detail(exc)}
        return result

    # --------------------------------------------------------------------- AI
    def ai_check(self, _body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Try Cursor and Claude with a tiny real prompt (in parallel; up to a minute or so)."""
        with ThreadPoolExecutor(max_workers=2) as pool:
            caps = dict(
                zip(health.PROVIDERS, pool.map(self.deps.prober, health.PROVIDERS), strict=True)
            )
        self.paths.ensure()
        store = Store.open(self.paths.db_path)
        try:
            health.save(store, caps)  # the daemon then only reports later changes
        finally:
            store.close()
        return {
            name: {
                "label": health.PROVIDERS[name],
                "state": cap.state,
                "detail": cap.detail,
                "login": LOGIN_COMMANDS[name],
            }
            for name, cap in caps.items()
        }

    def ai(self, body: dict[str, Any]) -> dict[str, Any]:
        """Choose which AI writes the drafts ("none" = facts only)."""
        provider = str(body.get("provider", ""))
        if provider not in {*health.PROVIDERS, "none"}:
            raise SetupError("choose Cursor, Claude Code or facts only")
        self._write(
            {"digest": {"provider": provider}, "transmit": {"llm_enabled": provider != "none"}}
        )
        return {"provider": provider}

    # ------------------------------------------------------------------ hooks
    def hooks(self, body: dict[str, Any]) -> dict[str, Any]:
        """Install the agent hooks you agreed to (each settings file is backed up first)."""
        try:
            script = installer.install_hook_script(self.paths)
            command = installer.hook_command
            python = self.deps.hook_python()
            done = {}
            if body.get("claude"):
                installer.install_claude(
                    self.deps.claude_settings, command(python, script, "claude_code")
                )
                done["claude"] = True
            if body.get("cursor"):
                installer.install_cursor(self.deps.cursor_hooks, command(python, script, "cursor"))
                done["cursor"] = True
        except installer.InstallError as exc:
            raise SetupError(str(exc)) from exc
        return done

    # ------------------------------------------------------------------ start
    def start(self, _body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Turn on cloud sync and start the background service."""
        if not self._signed_in():
            raise SetupError("create your account first (step 2)")
        self._write({"cloud": {"enabled": True}})
        executable = self.deps.executable() if self.deps.executable else _default_executable()
        try:
            self.deps.agent(self.paths).install(executable)
        except (FileNotFoundError, RuntimeError) as exc:
            raise SetupError(f"could not start the service: {exc}") from exc
        return {"started": True}

    def heartbeat(self, _body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Has this Mac's first heartbeat reached the cloud?"""
        config = self.config
        try:
            rest = Rest(self._auth(), self.team.publishable_key)
            rows = rest.select(
                "devices", {"device_id": f"eq.{config.cloud.device_id}", "select": "last_seen_at"}
            )
        except (CloudError, HttpError) as exc:
            return {"online": False, "detail": str(exc)}
        seen = rows[0].get("last_seen_at") if rows else None
        online = (
            bool(seen) and self.deps.clock() - datetime.fromisoformat(str(seen)) < HEARTBEAT_FRESH
        )
        return {"online": online, "last_seen_at": seen}

    def finish(self, _body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Close the setup page's server."""
        self.finished = True
        return {"bye": True}

    # ----------------------------------------------------------------- routes
    def routes(self) -> dict[tuple[str, str], Callable[..., dict[str, Any]]]:
        """(method, path) → action."""
        return {
            ("GET", "/api/state"): lambda _b: self.state(),
            ("POST", "/api/account"): self.account,
            ("POST", "/api/account/confirmed"): self.confirmed,
            ("POST", "/api/profile"): self.profile,
            ("POST", "/api/conversation/preview"): self.conversation_preview,
            ("POST", "/api/conversation/apply"): self.conversation_apply,
            ("POST", "/api/repos/scan"): self.repos_scan,
            ("POST", "/api/repos"): self.repos,
            ("POST", "/api/atlassian"): self.atlassian,
            ("POST", "/api/atlassian/check"): self.atlassian_check,
            ("POST", "/api/ai/check"): self.ai_check,
            ("POST", "/api/ai"): self.ai,
            ("POST", "/api/hooks"): self.hooks,
            ("POST", "/api/start"): self.start,
            ("GET", "/api/heartbeat"): self.heartbeat,
            ("POST", "/api/finish"): self.finish,
        }


def _default_executable() -> Path:
    from workdigest.service_cli import default_executable  # noqa: PLC0415 - avoids a CLI import cycle

    return default_executable()


def _http_detail(exc: HttpError) -> str:
    if exc.status in {401, 403}:
        return "the token was refused: check the email and the token's scopes"
    return str(exc)[:200]


def _auth_message(exc: Exception, mode: str) -> str:
    text = str(exc).lower()
    if "already registered" in text or "already exists" in text:
        return "this email already has an account: choose “I already have an account”"
    if "invalid login" in text or "invalid_grant" in text or "invalid credentials" in text:
        return "wrong email or password"
    if "email not confirmed" in text:
        return "confirm your email first: open the link Supabase sent you"
    if "database error" in text:
        return "sign-ups are limited to company email addresses"
    return f"could not {'create the account' if mode == 'signup' else 'sign in'}: {exc}"[:240]
