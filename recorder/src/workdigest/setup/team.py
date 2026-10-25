"""The team's shared settings: one Supabase project, the company domain, the website.

A release writes ``team.json`` into the package from the repository's GitHub variables (it is
not committed: the project URL stays out of the public repo). Without it, for a developer's
own build, the values come from ``$WORKDIGEST_TEAM_FILE`` or from the current config.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from workdigest.config import Config


@dataclass(frozen=True, slots=True)
class Team:
    """Everything a new teammate's Mac needs to join."""

    cloud_url: str = ""
    publishable_key: str = ""  # public by design; row-level security protects the data
    domain: str = "ei.study"
    site_url: str = ""
    jira_site: str = "https://ei-study.atlassian.net"
    bitbucket_workspace: str = "ei-india-admin"

    @property
    def apk_url(self) -> str:
        """Where the phone app is downloaded (always the latest release)."""
        return f"{self.site_url.rstrip('/')}/download/android" if self.site_url else ""

    def allows(self, email: str) -> bool:
        """Whether ``email`` may sign up (the database enforces the same rule)."""
        return email.strip().lower().endswith("@" + self.domain.lower())


def _read(raw: str) -> dict[str, Any]:
    data = json.loads(raw)
    return {k: v for k, v in data.items() if k in Team.__dataclass_fields__ and isinstance(v, str)}


def load_team(config: Config) -> Team:
    """The packaged team file, else ``$WORKDIGEST_TEAM_FILE``, else this Mac's config."""
    packaged = resources.files("workdigest.setup").joinpath("team.json")
    if packaged.is_file():
        return Team(**_read(packaged.read_text(encoding="utf-8")))
    env = os.environ.get("WORKDIGEST_TEAM_FILE")
    if env and Path(env).is_file():
        return Team(**_read(Path(env).read_text(encoding="utf-8")))
    return Team(
        cloud_url=config.cloud.url,
        publishable_key=config.cloud.publishable_key,
        jira_site=config.atlassian.jira_site,
        bitbucket_workspace=config.atlassian.bitbucket_workspace,
    )
