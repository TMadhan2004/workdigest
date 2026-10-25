"""Install the daemon as a per-user launchd agent (starts at login, restarted if it exits).

The agent runs ``<venv>/bin/digest daemon`` with a PATH that includes Homebrew, ~/.local/bin
(cursor-agent) and the system tools git needs. It is a *user* agent in
``~/Library/LaunchAgents``: no root, no system-wide change, and ``digest service stop``
removes it completely.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from workdigest.paths import Paths

LABEL = "com.workdigest.daemon"
LAUNCH_AGENTS = Path("~/Library/LaunchAgents").expanduser()
SEARCH_PATH = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    str(Path("~/.local/bin").expanduser()),
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
]

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False, timeout=30)


def plist_for(executable: Path, paths: Paths) -> dict[str, object]:
    """The launchd job definition."""
    return {
        "Label": LABEL,
        "ProgramArguments": [str(executable), "daemon"],
        "RunAtLoad": True,
        "KeepAlive": True,
        # Back off when the daemon keeps failing (seconds between restarts).
        "ThrottleInterval": 30,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "EnvironmentVariables": {
            "PATH": ":".join(SEARCH_PATH),
            "WORKDIGEST_HOME": str(paths.home),
            "PYTHONUNBUFFERED": "1",
        },
        "StandardOutPath": str(paths.log_dir / "launchd.out.log"),
        "StandardErrorPath": str(paths.log_dir / "launchd.err.log"),
    }


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    """Whether the agent is installed and running."""

    installed: bool
    loaded: bool
    pid: int | None
    last_exit: str | None


class LaunchAgent:
    """Install, remove and inspect the launchd agent."""

    def __init__(
        self,
        paths: Paths,
        *,
        agents_dir: Path = LAUNCH_AGENTS,
        runner: Runner = _run,
        uid: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """``agents_dir``, ``runner`` and ``sleep`` are injectable for tests."""
        self.paths = paths
        self.plist_path = agents_dir / f"{LABEL}.plist"
        self._run = runner
        self._sleep = sleep
        self._domain = f"gui/{uid if uid is not None else os.getuid()}"

    def _wait_unloaded(self, timeout_s: float = 15) -> None:
        """`bootout` returns before the old daemon has exited; bootstrapping meanwhile fails."""
        waited = 0.0
        while (
            waited < timeout_s
            and self._run(["launchctl", "print", f"{self._domain}/{LABEL}"]).returncode == 0
        ):
            self._sleep(0.25)
            waited += 0.25

    def install(self, executable: Path) -> Path:
        """Write the plist and (re)load it. Returns the plist path."""
        if not executable.is_file():
            raise FileNotFoundError(f"{executable} not found; run `make setup` first")
        self.paths.ensure()
        self.plist_path.parent.mkdir(parents=True, exist_ok=True)
        self._run(["launchctl", "bootout", f"{self._domain}/{LABEL}"])  # ignore "not loaded"
        self._wait_unloaded()
        with self.plist_path.open("wb") as handle:
            plistlib.dump(plist_for(executable, self.paths), handle)
        result = self._run(["launchctl", "bootstrap", self._domain, str(self.plist_path)])
        if result.returncode != 0:
            raise RuntimeError(
                f"launchctl bootstrap failed: {result.stderr.strip() or result.stdout.strip()}"
            )
        return self.plist_path

    def uninstall(self) -> bool:
        """Stop and remove the agent; True if it was installed."""
        existed = self.plist_path.exists()
        self._run(["launchctl", "bootout", f"{self._domain}/{LABEL}"])
        self.plist_path.unlink(missing_ok=True)
        return existed

    def status(self) -> ServiceStatus:
        """Read state from `launchctl print`."""
        result = self._run(["launchctl", "print", f"{self._domain}/{LABEL}"])
        if result.returncode != 0:
            return ServiceStatus(self.plist_path.exists(), loaded=False, pid=None, last_exit=None)
        pid = None
        last_exit = None
        for line in result.stdout.splitlines():
            key, _, value = line.strip().partition(" = ")
            if key == "pid" and value.isdigit():
                pid = int(value)
            elif key == "last exit code":
                last_exit = value
        return ServiceStatus(self.plist_path.exists(), loaded=True, pid=pid, last_exit=last_exit)
