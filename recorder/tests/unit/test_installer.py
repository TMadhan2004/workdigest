"""Hook installer: merge, idempotence, uninstall, backups, refusal on bad JSON."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workdigest.hooks import installer
from workdigest.paths import HOOK_SCRIPT_NAME, Paths

CMD = f"/opt/homebrew/bin/python3 '/x/WorkDigest/bin/{HOOK_SCRIPT_NAME}' claude_code"
CURSOR_CMD = CMD.replace("claude_code", "cursor")

USER_SETTINGS = {
    "model": "opus",
    "permissions": {"allow": ["Bash(go test *)"]},
    "hooks": {
        "PostToolUse": [
            {"matcher": "Edit", "hooks": [{"type": "command", "command": "prettier --write"}]}
        ],
    },
}


def _read(path: Path) -> dict[str, object]:
    return json.loads(path.read_text())


def test_claude_install_merges_and_preserves(tmp_path: Path) -> None:
    """TC-INS-001: Installing into Claude settings keeps all existing keys and the user's own hooks.

    Given: a settings.json with model, permissions and a user PostToolUse hook.
    When: install_claude() runs.
    Then: the original keys and the user hook are intact, all 6 events route to WorkDigest
          (async except SessionEnd), and a backup with the original content exists.
    """
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(USER_SETTINGS))
    backup = installer.install_claude(path, CMD)
    data = _read(path)
    assert data["model"] == "opus"
    assert data["permissions"] == USER_SETTINGS["permissions"]
    post = data["hooks"]["PostToolUse"]  # type: ignore[index]
    assert post[0] == USER_SETTINGS["hooks"]["PostToolUse"][0]  # type: ignore[index]
    assert post[1]["matcher"] == "*"
    status = installer.claude_status(path)
    assert status.complete
    assert data["hooks"]["SessionEnd"][0]["hooks"][0].get("async") is None  # type: ignore[index]
    assert data["hooks"]["Stop"][0]["hooks"][0]["async"] is True  # type: ignore[index]
    assert backup is not None
    assert json.loads(backup.read_text()) == USER_SETTINGS


def test_claude_install_is_idempotent(tmp_path: Path) -> None:
    """TC-INS-002: Installing twice leaves exactly one WorkDigest entry per event.

    Given: an empty settings file.
    When: install_claude() runs twice.
    Then: each event has one WorkDigest hook entry.
    """
    path = tmp_path / "settings.json"
    installer.install_claude(path, CMD)
    installer.install_claude(path, CMD)
    hooks = _read(path)["hooks"]
    for event in installer.CLAUDE_EVENTS:
        entries = [e for g in hooks[event] for e in g["hooks"] if HOOK_SCRIPT_NAME in e["command"]]  # type: ignore[index]
        assert len(entries) == 1, event


def test_claude_uninstall_removes_only_ours(tmp_path: Path) -> None:
    """TC-INS-003: Uninstall removes only WorkDigest entries and restores the user's structure.

    Given: settings with a user hook plus installed WorkDigest hooks.
    When: uninstall_claude() runs, then runs again.
    Then: the result equals the original settings, and the second run is a no-op returning None.
    """
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(USER_SETTINGS))
    installer.install_claude(path, CMD)
    assert installer.uninstall_claude(path) is not None
    assert _read(path) == USER_SETTINGS
    assert installer.uninstall_claude(path) is None


def test_claude_uninstall_drops_empty_hooks_key(tmp_path: Path) -> None:
    """TC-INS-004: When WorkDigest was the only hook user, uninstall removes the empty hooks object.

    Given: {"model": "x"} with WorkDigest installed.
    When: uninstalled.
    Then: the file is back to {"model": "x"}.
    """
    path = tmp_path / "settings.json"
    path.write_text('{"model": "x"}')
    installer.install_claude(path, CMD)
    installer.uninstall_claude(path)
    assert _read(path) == {"model": "x"}


@pytest.mark.parametrize("content", ["{ not json", "[1, 2]", '{"hooks": []}'])
def test_install_refuses_unparseable_settings(tmp_path: Path, content: str) -> None:
    """TC-INS-005: Invalid or unexpected settings are never overwritten.

    Given: settings that are not JSON, not an object, or have a non-object hooks value.
    When: install_claude() runs.
    Then: InstallError is raised and the file is byte-for-byte unchanged.
    """
    path = tmp_path / "settings.json"
    path.write_text(content)
    with pytest.raises(installer.InstallError):
        installer.install_claude(path, CMD)
    assert path.read_text() == content


def test_install_preserves_file_mode(tmp_path: Path) -> None:
    """TC-INS-006: The atomic rewrite keeps the original file permissions.

    Given: settings.json with mode 0640.
    When: install_claude() runs.
    Then: the file still has mode 0640 and no temp files are left behind.
    """
    path = tmp_path / "settings.json"
    path.write_text("{}")
    path.chmod(0o640)
    installer.install_claude(path, CMD)
    assert path.stat().st_mode & 0o777 == 0o640
    assert not list(tmp_path.glob(".settings.json.*"))


def test_cursor_install_creates_and_uninstalls(tmp_path: Path) -> None:
    """TC-INS-007: Cursor hooks.json is created with version 1, merges with user hooks and uninstalls cleanly.

    Given: no hooks.json, then one that also has a user afterFileEdit hook.
    When: install_cursor() runs, then uninstall_cursor().
    Then: all 9 events are installed, the user hook survives, and uninstall leaves only the user hook.
    """
    path = tmp_path / ".cursor" / "hooks.json"
    assert installer.install_cursor(path, CURSOR_CMD) is None  # nothing to back up
    data = _read(path)
    assert data["version"] == 1
    assert installer.cursor_status(path).complete
    data["hooks"]["afterFileEdit"].insert(0, {"command": "./format.sh"})  # type: ignore[index]
    path.write_text(json.dumps(data))
    installer.install_cursor(path, CURSOR_CMD)
    assert len(_read(path)["hooks"]["afterFileEdit"]) == 2  # type: ignore[index]
    installer.uninstall_cursor(path)
    assert _read(path) == {"version": 1, "hooks": {"afterFileEdit": [{"command": "./format.sh"}]}}
    assert installer.uninstall_cursor(path) is None
    assert installer.uninstall_cursor(tmp_path / "missing.json") is None


def test_status_on_missing_files(tmp_path: Path) -> None:
    """TC-INS-008: Status on missing files reports not installed, without raising.

    Given: no settings files.
    When: claude_status and cursor_status run.
    Then: both report exists=False and complete=False.
    """
    for status in (
        installer.claude_status(tmp_path / "a.json"),
        installer.cursor_status(tmp_path / "b.json"),
    ):
        assert not status.exists
        assert not status.complete


def test_hook_script_install_and_command_quoting(paths: Paths) -> None:
    """TC-INS-009: The hook script is copied privately, and the command quotes paths with spaces.

    Given: a WorkDigest home path containing a space ("Application Support").
    When: install_hook_script() runs and hook_command() builds the command.
    Then: the script exists with mode 0700, matches the packaged source, and its path is shell-quoted.
    """
    script = installer.install_hook_script(paths)
    assert script.stat().st_mode & 0o777 == 0o700
    assert "def main(argv" in script.read_text()
    spaced = Path("/Users/me/Library/Application Support/WorkDigest/bin") / HOOK_SCRIPT_NAME
    command = installer.hook_command("/opt/homebrew/bin/python3", spaced, "cursor")
    assert command == f"/opt/homebrew/bin/python3 '{spaced}' cursor"


def test_default_python_avoids_virtualenvs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TC-INS-010: The hook interpreter is a stable system python, not a project venv.

    Given: candidate list [missing, executable stable python]; then no candidates and python3 on PATH; then nothing.
    When: default_python() runs.
    Then: the first existing candidate wins, PATH is the fallback, and with nothing available InstallError is raised.
    """
    stable = tmp_path / "python3"
    stable.write_text("#!/bin/sh\n")
    stable.chmod(0o755)
    assert installer.default_python((str(tmp_path / "missing"), str(stable))) == str(stable)
    monkeypatch.setattr(installer.shutil, "which", lambda _: "/somewhere/python3")
    assert installer.default_python(()) == "/somewhere/python3"
    monkeypatch.setattr(installer.shutil, "which", lambda _: None)
    with pytest.raises(installer.InstallError):
        installer.default_python(())
