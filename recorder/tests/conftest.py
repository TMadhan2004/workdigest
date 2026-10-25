"""Shared fixtures: isolated WorkDigest home, store, config and event factory."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from workdigest.config import Config
from workdigest.hooks import hook_entry
from workdigest.ingest.normalize import EventFactory
from workdigest.paths import Paths
from workdigest.pipeline import build_factory
from workdigest.store.db import Store

IST = timezone(timedelta(hours=5, minutes=30))
HOOK_SCRIPT = Path(hook_entry.__file__)
ME = "arjun.k@ei.study"


class MemoryKeyring:
    """In-memory keyring so tests never touch the real macOS Keychain."""

    priority = 1

    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.data.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.data[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        self.data.pop((service_name, username), None)


@pytest.fixture(autouse=True)
def isolated_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No test ever touches the real WorkDigest home (e.g. the auth lock file)."""
    monkeypatch.setenv("WORKDIGEST_HOME", str(tmp_path_factory.mktemp("workdigest-home")))


class _NoCliProvider:
    """Stands in for the real AI CLIs in tests: never launches cursor-agent or claude."""

    binary_path = None
    name = "none"


@pytest.fixture(autouse=True)
def no_real_ai_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """Health probes see the AI CLIs as not installed (tests never call real AI tools)."""
    monkeypatch.setattr("workdigest.llm.health._provider", lambda _name: _NoCliProvider())


@pytest.fixture(autouse=True)
def memory_keyring(monkeypatch: pytest.MonkeyPatch) -> MemoryKeyring:
    fake = MemoryKeyring()
    monkeypatch.setattr("keyring.get_keyring", lambda: fake)
    return fake


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Paths:
    home = tmp_path / "home"
    monkeypatch.setenv("WORKDIGEST_HOME", str(home))
    resolved = Paths.resolve()
    resolved.ensure()
    return resolved


@pytest.fixture
def store(paths: Paths) -> Iterator[Store]:
    opened = Store.open(paths.db_path)
    yield opened
    opened.close()


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config.model_validate(
        {
            "identity": {"name": "Arjun K", "author_emails": [ME]},
            "digest": {"manager_name": "Priya"},
            "sources": {
                "claude_projects_dir": str(tmp_path / "claude-projects"),
                "cursor_projects_dir": str(tmp_path / "cursor-projects"),
                "backfill_days": 2,
            },
        }
    )


@pytest.fixture
def factory(config: Config) -> EventFactory:
    return build_factory(config)


def run_hook(
    payload: dict[str, Any] | str | bytes,
    source: str = "claude_code",
    env: dict[str, str] | None = None,
    timeout: float = 10,
) -> subprocess.CompletedProcess[bytes]:
    """Invoke the real hook script the way an agent does (stdin JSON, argv source)."""
    if isinstance(payload, dict):
        data = json.dumps(payload).encode()
    elif isinstance(payload, str):
        data = payload.encode()
    else:
        data = payload
    return subprocess.run(
        [sys.executable, str(HOOK_SCRIPT), source],
        input=data,
        capture_output=True,
        timeout=timeout,
        env=env,
        check=False,
    )


def claude_payload(
    event: str, session: str = "sess-A", cwd: str = "/tmp", **extra: Any
) -> dict[str, Any]:
    return {
        "session_id": session,
        "transcript_path": f"/Users/me/.claude/projects/x/{session}.jsonl",
        "cwd": cwd,
        "hook_event_name": event,
        **extra,
    }


def cursor_payload(event: str, conversation: str = "conv-1", **extra: Any) -> dict[str, Any]:
    return {
        "conversation_id": conversation,
        "generation_id": extra.pop("generation_id", "gen-1"),
        "model": "auto",
        "hook_event_name": event,
        "cursor_version": "3.24.12",
        "workspace_roots": [extra.pop("root", "/tmp")],
        "user_email": ME,
        "transcript_path": None,
        **extra,
    }


def spool_line(payload: dict[str, Any], received_at: datetime, source: str = "claude_code") -> str:
    return json.dumps(
        {
            "v": 1,
            "source_hint": source,
            "received_at": received_at.isoformat(),
            "truncated": False,
            "payload": payload,
        }
    )


@pytest.fixture
def write_spool(paths: Paths) -> Callable[..., Path]:
    def _write(
        lines: list[str], name: str = "2026-10-23.jsonl", newline_at_end: bool = True
    ) -> Path:
        path = paths.spool_dir / name
        text = "\n".join(lines) + ("\n" if newline_at_end else "")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text)
        return path

    return _write


def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    base = {
        "GIT_AUTHOR_NAME": "Arjun K",
        "GIT_AUTHOR_EMAIL": ME,
        "GIT_COMMITTER_NAME": "Arjun K",
        "GIT_COMMITTER_EMAIL": ME,
        "HOME": str(repo.parent),
        "GIT_CONFIG_NOSYSTEM": "1",
        "PATH": "/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin",
    }
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env={**base, **(env or {})},
        check=True,
    )
    return completed.stdout
