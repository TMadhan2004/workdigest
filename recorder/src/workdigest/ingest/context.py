"""Enrichment helpers: ticket keys, and repo or branch lookup for a working directory."""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

_GIT_TIMEOUT_S = 3
_CACHE_TTL_S = 60.0


class TicketMatcher:
    """Finds Jira keys such as ``EIT-8403`` for the configured projects only.

    An allowlist avoids false positives such as ``UTF-8``, ``SHA-256`` or test IDs
    like ``TC-UN-9701``.
    """

    def __init__(self, project_keys: Iterable[str]) -> None:
        """Compile a matcher for ``project_keys`` (an empty list matches nothing)."""
        keys = sorted({key.strip().upper() for key in project_keys if key.strip()})
        self._pattern = (
            re.compile(
                r"(?<![A-Za-z0-9-])(" + "|".join(map(re.escape, keys)) + r")-(\d{1,7})(?!\d)", re.I
            )
            if keys
            else None
        )

    def find(self, *texts: str | None) -> list[str]:
        """Return the distinct ticket keys in ``texts``, in first-seen order."""
        if self._pattern is None:
            return []
        seen: dict[str, None] = {}
        for text in texts:
            if not text:
                continue
            for match in self._pattern.finditer(text):
                seen.setdefault(f"{match.group(1).upper()}-{match.group(2)}", None)
        return list(seen)


@dataclass(frozen=True, slots=True)
class RepoContext:
    """Git facts about a working directory."""

    root: str | None
    name: str | None
    branch: str | None


class RepoResolver:
    """Maps a directory to its git root and current branch, with a short cache."""

    def __init__(self, ttl_s: float = _CACHE_TTL_S) -> None:
        """Create a resolver whose answers stay valid for ``ttl_s`` seconds."""
        self._ttl = ttl_s
        self._cache: dict[str, tuple[float, RepoContext]] = {}

    def resolve(self, cwd: str | None) -> RepoContext:
        """Return the repo context for ``cwd`` (all ``None`` outside a repo)."""
        if not cwd:
            return RepoContext(None, None, None)
        cached = self._cache.get(cwd)
        now = time.monotonic()
        if cached and now - cached[0] < self._ttl:
            return cached[1]
        context = self._lookup(cwd)
        self._cache[cwd] = (now, context)
        return context

    @staticmethod
    def _lookup(cwd: str) -> RepoContext:
        if not Path(cwd).is_dir():
            return RepoContext(None, None, None)
        root = _git(cwd, "rev-parse", "--show-toplevel")
        if root is None:
            return RepoContext(None, None, None)
        branch = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
        return RepoContext(
            root=root, name=Path(root).name, branch=None if branch == "HEAD" else branch
        )


def _git(cwd: str, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None
