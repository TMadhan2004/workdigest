"""The installed version (one source: the package metadata written from pyproject.toml)."""

from __future__ import annotations

from importlib import metadata

try:
    __version__ = metadata.version("workdigest")
except metadata.PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0"
