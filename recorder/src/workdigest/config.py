"""User configuration, loaded from ``config.toml`` and validated with pydantic."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Identity(_Strict):
    """Who "me" is, for matching commits and tickets."""

    name: str = ""
    author_emails: list[str] = Field(default_factory=list)

    @field_validator("author_emails")
    @classmethod
    def _lowercase(cls, value: list[str]) -> list[str]:
        return [email.strip().lower() for email in value if email.strip()]


class DaySettings(_Strict):
    """How events are grouped into work days."""

    boundary_hour: Annotated[int, Field(ge=0, le=12)] = 4
    idle_timeout_minutes: Annotated[int, Field(ge=1)] = 30


class RepoConfig(_Strict):
    """A git repository the poller watches."""

    path: Path
    name: str | None = None

    @field_validator("path")
    @classmethod
    def _expand(cls, value: Path) -> Path:
        return value.expanduser()

    @property
    def display_name(self) -> str:
        """Configured name, else the folder name."""
        return self.name or self.path.name


class CaptureSettings(_Strict):
    """How much text is kept locally per event (after redaction)."""

    prompt_excerpt_chars: Annotated[int, Field(ge=0)] = 600
    tool_output_tail_chars: Annotated[int, Field(ge=0)] = 4000
    agent_text_chars: Annotated[int, Field(ge=0)] = 1500
    command_chars: Annotated[int, Field(ge=0)] = 1000
    # Folders whose agent sessions are not your reportable work (e.g. this tool's own repo).
    exclude_paths: list[Path] = Field(default_factory=list)

    @field_validator("exclude_paths")
    @classmethod
    def _expand_excludes(cls, value: list[Path]) -> list[Path]:
        return [path.expanduser() for path in value]


class RedactionSettings(_Strict):
    """Extra redaction rules on top of the built-in ones."""

    extra_patterns: list[str] = Field(default_factory=list)
    sensitive_path_globs: list[str] = Field(
        default_factory=lambda: [
            "*.env",
            ".env",
            ".env.*",
            "*.pem",
            "*.key",
            "*.p12",
            "id_rsa*",
            "id_ed25519*",
            "*credentials*",
            "*.keystore",
        ]
    )
    safe_path_globs: list[str] = Field(
        default_factory=lambda: [".env.example", ".env.sample", ".env.template"]
    )


class SourceSettings(_Strict):
    """Where agent transcripts live."""

    # validate_default: pydantic skips validators on defaults, and "~" must still be expanded.
    claude_projects_dir: Path = Field(default=Path("~/.claude/projects"), validate_default=True)
    cursor_projects_dir: Path = Field(default=Path("~/.cursor/projects"), validate_default=True)
    backfill_days: Annotated[int, Field(ge=0, le=30)] = 2

    @field_validator("claude_projects_dir", "cursor_projects_dir")
    @classmethod
    def _expand(cls, value: Path) -> Path:
        return value.expanduser()


class JiraSettings(_Strict):
    """Ticket-key detection (API access arrives in Phase 3)."""

    project_keys: list[str] = Field(default_factory=lambda: ["EIT", "CA"])


class AtlassianSettings(_Strict):
    """Read-only Jira Cloud and Bitbucket Cloud access (tokens live in the Keychain)."""

    enabled: bool = True
    email: str = ""  # Atlassian account email; defaults to the first identity email
    jira_site: str = "https://ei-study.atlassian.net"
    # Scoped API tokens must call the API gateway with the site's cloud id (public, not a secret).
    # Looked up from the site when empty (public `<site>/_edge/tenant_info`); set it to skip that.
    jira_cloud_id: str = ""
    bitbucket_workspace: str = "ei-india-admin"
    timeout_s: Annotated[int, Field(ge=5, le=120)] = 20


class CloudSettings(_Strict):
    """Supabase sync. Off until `digest cloud setup` and `digest cloud login` have run."""

    enabled: bool = False
    url: str = ""  # https://<project-ref>.supabase.co
    publishable_key: str = (
        ""  # public by design (anon/publishable); the service-role key is never used
    )
    email: str = ""
    device_id: str = ""
    push_days: Annotated[int, Field(ge=1, le=60)] = 14
    # What may be uploaded (all redacted). Company policy on code-derived data must allow it.
    sync_session_summaries: bool = True
    sync_evidence: bool = True
    sync_replies: bool = True


class TransmitSettings(_Strict):
    """What may leave the Mac. Company policy on code-derived data must allow it first."""

    llm_enabled: bool = True
    prompt_excerpts: bool = True  # your prompts (redacted, capped) in session summaries
    agent_messages: bool = True  # the agents' own messages (redacted, capped)
    file_paths: bool = True  # names of files edited
    commit_subjects: bool = True


class DigestSettings(_Strict):
    """Digest generation."""

    provider: str = Field(default="cursor_cli", pattern=r"^(cursor_cli|claude_cli|none)$")
    model: str | None = None
    timeout_s: Annotated[int, Field(ge=30, le=1800)] = 300
    manager_name: str = ""  # asked during `digest setup`
    project: str = ""
    team_members: list[str] = Field(default_factory=list)
    send_deadline: str = Field(default="21:00", pattern=r"^\d{2}:\d{2}$")
    # Local evening draft + desktop nudge (cloud users get the time from Supabase settings).
    nudge_time: str = Field(default="20:00", pattern=r"^\d{2}:\d{2}$")


class Config(_Strict):
    """Root configuration object."""

    identity: Identity = Field(default_factory=Identity)
    day: DaySettings = Field(default_factory=DaySettings)
    repos: list[RepoConfig] = Field(default_factory=list)
    capture: CaptureSettings = Field(default_factory=CaptureSettings)
    redaction: RedactionSettings = Field(default_factory=RedactionSettings)
    sources: SourceSettings = Field(default_factory=SourceSettings)
    jira: JiraSettings = Field(default_factory=JiraSettings)
    digest: DigestSettings = Field(default_factory=DigestSettings)
    transmit: TransmitSettings = Field(default_factory=TransmitSettings)
    atlassian: AtlassianSettings = Field(default_factory=AtlassianSettings)
    cloud: CloudSettings = Field(default_factory=CloudSettings)

    @property
    def atlassian_email(self) -> str:
        """Email used for Atlassian Basic auth."""
        return self.atlassian.email or (
            self.identity.author_emails[0] if self.identity.author_emails else ""
        )


class ConfigError(Exception):
    """The configuration file is missing or invalid."""


def load_config(path: Path) -> Config:
    """Load and validate ``path``. A missing file yields the defaults."""
    if not path.exists():
        return Config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
    _apply_cloud_overrides(data, path.parent / "cloud_settings.json")
    try:
        return Config.model_validate(data)
    except ValueError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def _apply_cloud_overrides(data: dict[str, Any], overrides: Path) -> None:
    """Settings changed in the app (provider, transmit flags) win over config.toml."""
    if not overrides.exists():
        return
    try:
        cloud = json.loads(overrides.read_text(encoding="utf-8"))
    except ValueError:
        return  # a broken overrides file never blocks the recorder
    provider = cloud.get("provider")
    if provider in {"cursor_cli", "claude_cli", "none"}:
        data["digest"] = {**(data.get("digest") or {}), "provider": provider}
    transmit = {
        key: value
        for key, value in (cloud.get("transmit") or {}).items()
        if key in TransmitSettings.model_fields and isinstance(value, bool)
    }
    if transmit:
        data["transmit"] = {**(data.get("transmit") or {}), **transmit}


DEFAULT_CONFIG_TOML = """\
# WorkDigest configuration. Edit, then run `digest doctor` to validate.

[identity]
name = "{name}"
author_emails = [{emails}]

[day]
# Events before this hour count toward the previous work day.
boundary_hour = 4
idle_timeout_minutes = 30

# One [[repos]] block per repository to watch. Example:
# [[repos]]
# path = "~/Desktop/microservices-redesign/billing-service"

[capture]
prompt_excerpt_chars = 600
tool_output_tail_chars = 4000
agent_text_chars = 1500
command_chars = 1000
# Folders whose agent sessions are not reportable work, e.g. ["~/Desktop/archive/work_tracker"].
exclude_paths = []

[redaction]
extra_patterns = []

[sources]
claude_projects_dir = "~/.claude/projects"
cursor_projects_dir = "~/.cursor/projects"
backfill_days = 2

[jira]
project_keys = ["EIT", "CA"]

[digest]
# cursor_cli (your Cursor subscription via `cursor-agent`), claude_cli, or none (no LLM).
provider = "cursor_cli"
manager_name = ""
project = ""
team_members = []
send_deadline = "21:00"
nudge_time = "20:00"

# What may be sent to the LLM. Check your company's policy on code-derived data first.
[transmit]
llm_enabled = true
prompt_excerpts = true
agent_messages = true
file_paths = true
commit_subjects = true
"""


def render_default_config(name: str, emails: list[str]) -> str:
    """Return the starter ``config.toml`` text."""
    quoted = ", ".join(f'"{email}"' for email in emails)
    return DEFAULT_CONFIG_TOML.format(name=name, emails=quoted)
