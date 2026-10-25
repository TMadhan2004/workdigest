"""Domain models shared across collectors, the store and reports."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

EVENT_SCHEMA_VERSION = 1


class Source(StrEnum):
    """Where an event came from."""

    CLAUDE_CODE = "claude_code"
    CURSOR = "cursor"
    GIT = "git"
    JIRA = "jira"
    BITBUCKET = "bitbucket"
    MANUAL = "manual"


class Origin(StrEnum):
    """How an event was captured."""

    HOOK = "hook"
    TRANSCRIPT = "transcript"
    POLLER = "poller"
    API = "api"
    MANUAL = "manual"


class Kind(StrEnum):
    """Normalized event kinds."""

    SESSION_START = "session_start"
    SESSION_END = "session_end"
    PROMPT = "prompt"
    TOOL_USE = "tool_use"
    SHELL = "shell"
    FILE_EDIT = "file_edit"
    AGENT_RESPONSE = "agent_response"
    STOP = "stop"
    SUBAGENT_STOP = "subagent_stop"
    COMMIT = "commit"
    BRANCH_CREATED = "branch_created"
    DAY_CLOSED = "day_closed"


def canonical_json(value: Any) -> str:
    """Return deterministic JSON for hashing and storage."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def stable_hash(*parts: object) -> str:
    """Return a sha256 hex digest over ``parts``."""
    digest = hashlib.sha256()
    for part in parts:
        text = part if isinstance(part, str) else canonical_json(part)
        digest.update(text.encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()


class Event(BaseModel):
    """A normalized, redacted unit of evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    schema_version: int = EVENT_SCHEMA_VERSION
    source: Source
    origin: Origin
    kind: Kind
    ts: datetime
    ts_approx: bool = False
    work_date: str
    session_key: str | None = None
    cwd: str | None = None
    repo: str | None = None
    branch: str | None = None
    ticket_keys: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    redaction_rules: list[str] = Field(default_factory=list)

    @staticmethod
    def make_id(source: Source, session_key: str | None, kind: Kind, natural_key: str) -> str:
        """Derive the dedupe key shared by every capture path for the same fact."""
        return stable_hash(source.value, session_key or "", kind.value, natural_key)


class TestRun(BaseModel):
    """A parsed test-suite result."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    __test__ = False  # not a pytest test class

    runner: str
    passed: int
    failed: int
    skipped: int = 0
    total: int
    unit: str = "tests"
    summary_line: str

    @property
    def ok(self) -> bool:
        """Whether the run had no failures."""
        return self.failed == 0 and self.total > 0
