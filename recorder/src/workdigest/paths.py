"""Filesystem locations used by the recorder.

Everything lives under one home directory so a test (or a second install) can point the
whole system somewhere else with ``WORKDIGEST_HOME``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOME = Path("~/Library/Application Support/WorkDigest").expanduser()
HOOK_SCRIPT_NAME = "workdigest-hook.py"


@dataclass(frozen=True, slots=True)
class Paths:
    """Resolved locations under the WorkDigest home directory."""

    home: Path

    @classmethod
    def resolve(cls, home: Path | None = None) -> Paths:
        """Use ``home``, else ``$WORKDIGEST_HOME``, else the macOS default."""
        if home is None:
            env = os.environ.get("WORKDIGEST_HOME")
            home = Path(env).expanduser() if env else DEFAULT_HOME
        return cls(home=home)

    @property
    def spool_dir(self) -> Path:
        """Directory the hook script appends raw events to."""
        return self.home / "spool"

    @property
    def db_path(self) -> Path:
        """Local SQLite evidence store."""
        return self.home / "workdigest.sqlite3"

    @property
    def config_path(self) -> Path:
        """User configuration (TOML)."""
        return self.home / "config.toml"

    @property
    def bin_dir(self) -> Path:
        """Installed helper scripts (the hook entry point)."""
        return self.home / "bin"

    @property
    def hook_script(self) -> Path:
        """Installed copy of the hook entry point."""
        return self.bin_dir / HOOK_SCRIPT_NAME

    @property
    def log_dir(self) -> Path:
        """Daemon logs."""
        return self.home / "logs"

    def ensure(self) -> None:
        """Create the private directories with owner-only permissions."""
        for directory in (self.home, self.spool_dir, self.bin_dir, self.log_dir):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
