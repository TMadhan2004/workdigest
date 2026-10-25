"""The launchd agent (plist, install, uninstall, status) and the `digest daemon` / `digest service` commands."""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from workdigest import cli, service_cli
from workdigest.paths import Paths
from workdigest.service import LABEL, LaunchAgent, ServiceStatus, plist_for

runner = CliRunner()


def _no_sleep(_seconds: float) -> None:
    """Tests never wait for launchd."""


PRINT_RUNNING = """gui/501/com.workdigest.daemon = {
	active count = 1
	path = /Users/me/Library/LaunchAgents/com.workdigest.daemon.plist
	state = running
	pid = 4242
	last exit code = (never exited)
}
"""


class Launchctl:
    """Records launchctl calls and answers with canned results."""

    def __init__(self, bootstrap_rc: int = 0, print_out: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.bootstrap_rc = bootstrap_rc
        self.print_out = print_out

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        if argv[1] == "bootstrap":
            return subprocess.CompletedProcess(
                argv, self.bootstrap_rc, "", "Bootstrap failed: 5: Input/output error"
            )
        if argv[1] == "print":
            rc = 0 if self.print_out is not None else 113
            return subprocess.CompletedProcess(argv, rc, self.print_out or "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def executable(tmp_path: Path) -> Path:
    path = tmp_path / "venv" / "bin" / "digest"
    path.parent.mkdir(parents=True)
    path.write_text("#!/bin/sh\n")
    return path


def test_plist_definition(paths: Paths, executable: Path) -> None:
    """TC-SVC-001: The agent starts at login, restarts on exit, and can find git and cursor-agent.

    Given: the venv `digest` executable and a WorkDigest home.
    When: the plist is built.
    Then: it runs `digest daemon` with RunAtLoad and KeepAlive, a restart throttle, a PATH with
          Homebrew and ~/.local/bin, WORKDIGEST_HOME, and logs under <home>/logs.
    """
    plist = plist_for(executable, paths)
    assert plist["Label"] == LABEL
    assert plist["ProgramArguments"] == [str(executable), "daemon"]
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    assert plist["ThrottleInterval"] == 30
    env = plist["EnvironmentVariables"]
    assert isinstance(env, dict)
    assert "/opt/homebrew/bin" in env["PATH"]
    assert str(Path("~/.local/bin").expanduser()) in env["PATH"]
    assert env["WORKDIGEST_HOME"] == str(paths.home)
    assert str(plist["StandardErrorPath"]).startswith(str(paths.log_dir))


def test_install_and_uninstall(paths: Paths, executable: Path, tmp_path: Path) -> None:
    """TC-SVC-002: Install writes a valid plist and (re)loads it; uninstall unloads and deletes it.

    Given: an empty LaunchAgents directory and a recording launchctl.
    When: the agent is installed, then uninstalled twice.
    Then: the plist parses back, bootout precedes bootstrap in the gui/<uid> domain, the file is
          removed, and the second uninstall reports it was not installed.
    """
    launchctl = Launchctl()
    agent = LaunchAgent(
        paths, agents_dir=tmp_path / "LaunchAgents", runner=launchctl, uid=501, sleep=_no_sleep
    )
    plist_path = agent.install(executable)
    assert plistlib.loads(plist_path.read_bytes())["ProgramArguments"][1] == "daemon"
    assert [c[1] for c in launchctl.calls] == ["bootout", "print", "bootstrap"]
    assert launchctl.calls[2][2:] == ["gui/501", str(plist_path)]
    assert agent.uninstall() is True
    assert not plist_path.exists()
    assert launchctl.calls[-1] == ["launchctl", "bootout", f"gui/501/{LABEL}"]
    assert agent.uninstall() is False


def test_install_errors(paths: Paths, executable: Path, tmp_path: Path) -> None:
    """TC-SVC-003: A missing executable or a failed bootstrap is a clear error.

    Given: a path that does not exist, then a launchctl whose bootstrap fails.
    When: install runs.
    Then: FileNotFoundError mentioning `make setup`, then RuntimeError with launchctl's message.
    """
    agent = LaunchAgent(
        paths, agents_dir=tmp_path / "LA", runner=Launchctl(), uid=501, sleep=_no_sleep
    )
    with pytest.raises(FileNotFoundError, match="make setup"):
        agent.install(tmp_path / "nope")
    failing = LaunchAgent(
        paths,
        agents_dir=tmp_path / "LA",
        runner=Launchctl(bootstrap_rc=5),
        uid=501,
        sleep=_no_sleep,
    )
    with pytest.raises(RuntimeError, match="Input/output error"):
        failing.install(executable)


def test_status(paths: Paths, executable: Path, tmp_path: Path) -> None:
    """TC-SVC-004: Status reads pid and last exit from `launchctl print`, and reports an unloaded agent.

    Given: launchctl printing a running job, then launchctl saying the job is unknown.
    When: status() is read.
    Then: installed, loaded, pid 4242 and "(never exited)"; then not loaded with no pid.
    """
    agents = tmp_path / "LA"
    running = LaunchAgent(
        paths,
        agents_dir=agents,
        runner=Launchctl(print_out=PRINT_RUNNING),
        uid=501,
        sleep=_no_sleep,
    )
    running.install(executable)
    assert running.status() == ServiceStatus(
        installed=True, loaded=True, pid=4242, last_exit="(never exited)"
    )
    stopped = LaunchAgent(
        paths, agents_dir=tmp_path / "empty", runner=Launchctl(), uid=501, sleep=_no_sleep
    )
    assert stopped.status() == ServiceStatus(
        installed=False, loaded=False, pid=None, last_exit=None
    )


def test_service_commands(
    paths: Paths, executable: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-SVC-005: `digest service start|status|restart|stop` drive the agent and report plainly.

    Given: LaunchAgent redirected to a temp directory and a recording launchctl that reports a running pid.
    When: start, status, restart and stop run through the CLI; the old install/uninstall names too;
          then start with a missing executable.
    Then: exit codes 0 with readable output ("Started", "running (pid 4242)", "Stopped"), the old
          names still work, and 1 with the error for the missing executable.
    """
    launchctl = Launchctl(print_out=PRINT_RUNNING.replace("(never exited)", "78"))

    def agent(p: Paths) -> LaunchAgent:
        return LaunchAgent(
            p, agents_dir=tmp_path / "LA", runner=launchctl, uid=501, sleep=_no_sleep
        )

    monkeypatch.setattr(service_cli, "LaunchAgent", agent)
    started = runner.invoke(cli.app, ["service", "start", "--executable", str(executable)])
    assert started.exit_code == 0, started.output
    assert "Started" in started.output
    status = runner.invoke(cli.app, ["service", "status"])
    assert status.exit_code == 0
    assert "running (pid 4242)" in status.output
    assert "Last exit: 78" in status.output
    restarted = runner.invoke(cli.app, ["service", "restart", "--executable", str(executable)])
    assert "Started" in restarted.output
    stopped = runner.invoke(cli.app, ["service", "stop"])
    assert "Stopped" in stopped.output
    assert "digest service start" in stopped.output
    assert "not running" in runner.invoke(cli.app, ["service", "stop"]).output
    alias = runner.invoke(cli.app, ["service", "install", "--executable", str(executable)])
    assert "Started" in alias.output
    assert "Stopped" in runner.invoke(cli.app, ["service", "uninstall"]).output
    missing = runner.invoke(cli.app, ["service", "start", "--executable", str(tmp_path / "nope")])
    assert missing.exit_code == 1
    assert service_cli.default_executable().name == "digest"


def test_daemon_once_command(paths: Paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TC-SVC-006: `digest daemon --once` runs every due task once and exits; a bad config exits 2.

    Given: a local-only config with no LLM and no repos, then an invalid config file.
    When: `digest daemon --once` runs.
    Then: it lists the tasks it ran (spool, collect, atlassian, summarize, …) and exits 0; then exits 2 with "Config error".
    """
    monkeypatch.setattr("workdigest.daemon.MacNotifier.notify", lambda *_a: None)
    monkeypatch.setattr(service_cli, "setup_logging", lambda _p: None)
    paths.config_path.write_text(
        f'[digest]\nprovider = "none"\n[sources]\nclaude_projects_dir = "{tmp_path / "c"}"\n'
        f'cursor_projects_dir = "{tmp_path / "x"}"\n'
    )
    result = runner.invoke(cli.app, ["daemon", "--once"])
    assert result.exit_code == 0, result.output
    assert "ran: spool, collect, atlassian, summarize" in result.output
    paths.config_path.write_text("[digest]\nprovider = 'nope'\n")
    bad = runner.invoke(cli.app, ["daemon", "--once"])
    assert bad.exit_code == 2
    assert "Config error" in bad.output


def test_install_waits_for_the_old_agent_to_unload(
    paths: Paths, executable: Path, tmp_path: Path
) -> None:
    """TC-SVC-007: Reinstalling waits until the old daemon has really stopped before starting the new one.

    Given: a loaded agent that keeps showing up in `launchctl print` for three checks after bootout.
    When: install runs.
    Then: it waits (three short sleeps) and only then bootstraps, so the bootstrap does not collide.
    """
    prints = {"left": 3}
    calls: list[str] = []

    def launchctl(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv[1])
        if argv[1] == "print":
            prints["left"] -= 1
            return subprocess.CompletedProcess(argv, 0 if prints["left"] >= 0 else 113, "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    sleeps: list[float] = []
    agent = LaunchAgent(
        paths, agents_dir=tmp_path / "LA", runner=launchctl, uid=501, sleep=sleeps.append
    )
    agent.install(executable)
    assert calls == ["bootout", "print", "print", "print", "print", "bootstrap"]
    assert len(sleeps) == 3
