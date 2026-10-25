"""Install and uninstall WorkDigest hooks in Claude Code and Cursor settings.

The installer edits files the user owns and relies on, so it:

* never rewrites a file it cannot parse; it raises instead;
* backs up the original next to it (``<file>.workdigest-backup-<timestamp>``) before
  every write;
* writes atomically (a temp file, then ``os.replace``) and keeps the file mode;
* only touches entries whose command contains the WorkDigest hook script name, so
  re-running is idempotent and uninstall leaves the user's own hooks alone.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from workdigest.clock import now_local
from workdigest.paths import HOOK_SCRIPT_NAME, Paths

# Claude Code: event -> matcher (None = event takes no matcher).
CLAUDE_EVENTS: dict[str, str | None] = {
    "SessionStart": None,
    "UserPromptSubmit": None,
    "PostToolUse": "*",
    "Stop": None,
    "SubagentStop": None,
    "SessionEnd": None,
}
# SessionEnd runs synchronously: background work may be cut off when the session exits.
_CLAUDE_SYNC_EVENTS = {"SessionEnd"}
CURSOR_EVENTS: tuple[str, ...] = (
    "sessionStart",
    "sessionEnd",
    "beforeSubmitPrompt",
    "postToolUse",
    "afterShellExecution",
    "afterFileEdit",
    "afterAgentResponse",
    "stop",
    "subagentStop",
)
HOOK_TIMEOUT_S = 5


class InstallError(RuntimeError):
    """The settings file could not be safely updated."""


@dataclass(frozen=True, slots=True)
class HookStatus:
    """Which events currently route to WorkDigest in one settings file."""

    path: Path
    exists: bool
    installed_events: tuple[str, ...]
    expected_events: tuple[str, ...]

    @property
    def complete(self) -> bool:
        """Whether every expected event is hooked."""
        return set(self.expected_events) <= set(self.installed_events)


def install_hook_script(paths: Paths) -> Path:
    """Copy the stdlib-only hook entry point into ``<home>/bin``."""
    paths.ensure()
    source = (
        resources.files("workdigest.hooks").joinpath("hook_entry.py").read_text(encoding="utf-8")
    )
    target = paths.hook_script
    target.write_text(source, encoding="utf-8")
    target.chmod(0o700)
    return target


STABLE_PYTHONS = ("/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3")


def default_python(candidates: tuple[str, ...] = STABLE_PYTHONS) -> str:
    """Pick an interpreter that outlives project virtualenvs.

    ``uv run`` puts ``.venv/bin`` first on PATH, and a hook pointing there would break
    when that venv is rebuilt. The hook script needs only the standard library.
    """
    for candidate in candidates:
        if Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which("python3")
    if found is None:
        raise InstallError("no python3 interpreter found; pass --python")
    return found


def hook_command(python: str, script: Path, source: str) -> str:
    """Shell command the agent runs for each hook event."""
    return f"{shlex.quote(python)} {shlex.quote(str(script))} {source}"


# ----------------------------------------------------------------- Claude Code
def install_claude(settings_path: Path, command: str) -> Path | None:
    """Add WorkDigest hooks to a Claude Code ``settings.json``. Return the backup path."""
    data = _load(settings_path)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise InstallError(f"{settings_path}: 'hooks' is not an object")
    _remove_ours_claude(hooks)
    for event, matcher in CLAUDE_EVENTS.items():
        entry: dict[str, Any] = {"type": "command", "command": command, "timeout": HOOK_TIMEOUT_S}
        if event not in _CLAUDE_SYNC_EVENTS:
            entry["async"] = True
        group: dict[str, Any] = {"hooks": [entry]}
        if matcher is not None:
            group["matcher"] = matcher
        hooks.setdefault(event, []).append(group)
    return _save(settings_path, data)


def uninstall_claude(settings_path: Path) -> Path | None:
    """Remove only WorkDigest hooks from a Claude Code ``settings.json``."""
    if not settings_path.exists():
        return None
    data = _load(settings_path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return None
    if not _remove_ours_claude(hooks):
        return None
    if not hooks:
        data.pop("hooks")
    return _save(settings_path, data)


def claude_status(settings_path: Path) -> HookStatus:
    """Report which Claude Code events route to WorkDigest."""
    installed: list[str] = []
    if settings_path.exists():
        hooks = _load(settings_path).get("hooks")
        if isinstance(hooks, dict):
            for event, groups in hooks.items():
                for group in groups if isinstance(groups, list) else []:
                    entries = group.get("hooks", []) if isinstance(group, dict) else []
                    if any(_is_ours(e) for e in entries):
                        installed.append(event)
                        break
    return HookStatus(settings_path, settings_path.exists(), tuple(installed), tuple(CLAUDE_EVENTS))


def _remove_ours_claude(hooks: dict[str, Any]) -> bool:
    changed = False
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                kept_groups.append(group)
                continue
            entries = [e for e in group["hooks"] if not _is_ours(e)]
            if len(entries) != len(group["hooks"]):
                changed = True
            if entries:
                kept_groups.append({**group, "hooks": entries})
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]
    return changed


# ---------------------------------------------------------------------- Cursor
def install_cursor(hooks_path: Path, command: str) -> Path | None:
    """Add WorkDigest hooks to Cursor's ``hooks.json``. Return the backup path."""
    data = _load(hooks_path) if hooks_path.exists() else {"version": 1, "hooks": {}}
    data.setdefault("version", 1)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise InstallError(f"{hooks_path}: 'hooks' is not an object")
    _remove_ours_cursor(hooks)
    for event in CURSOR_EVENTS:
        hooks.setdefault(event, []).append({"command": command, "timeout": HOOK_TIMEOUT_S})
    return _save(hooks_path, data)


def uninstall_cursor(hooks_path: Path) -> Path | None:
    """Remove only WorkDigest hooks from Cursor's ``hooks.json``."""
    if not hooks_path.exists():
        return None
    data = _load(hooks_path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict) or not _remove_ours_cursor(hooks):
        return None
    return _save(hooks_path, data)


def cursor_status(hooks_path: Path) -> HookStatus:
    """Report which Cursor events route to WorkDigest."""
    installed: list[str] = []
    if hooks_path.exists():
        hooks = _load(hooks_path).get("hooks")
        if isinstance(hooks, dict):
            installed = [
                event
                for event, entries in hooks.items()
                if isinstance(entries, list) and any(_is_ours(e) for e in entries)
            ]
    return HookStatus(hooks_path, hooks_path.exists(), tuple(installed), CURSOR_EVENTS)


def _remove_ours_cursor(hooks: dict[str, Any]) -> bool:
    changed = False
    for event in list(hooks):
        entries = hooks[event]
        if not isinstance(entries, list):
            continue
        kept = [e for e in entries if not _is_ours(e)]
        if len(kept) != len(entries):
            changed = True
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    return changed


# --------------------------------------------------------------------- helpers
def _is_ours(entry: Any) -> bool:
    return isinstance(entry, dict) and HOOK_SCRIPT_NAME in str(entry.get("command", ""))


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise InstallError(f"{path} is not valid JSON ({exc}); refusing to modify it") from exc
    if not isinstance(data, dict):
        raise InstallError(f"{path}: top level is not a JSON object; refusing to modify it")
    return data


def _save(path: Path, data: dict[str, Any]) -> Path | None:
    backup = None
    mode = 0o600
    if path.exists():
        stamp = now_local().strftime("%Y%m%d-%H%M%S-%f")
        backup = path.with_name(f"{path.name}.workdigest-backup-{stamp}")
        shutil.copy2(path, backup)
        mode = path.stat().st_mode & 0o777
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        Path(tmp).chmod(mode)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return backup
