"""Is a newer WorkDigest out for the Mac? Read from the website's version.json.

The daemon checks once a day and announces each new version once (on the Mac, and in the app
when cloud sync is on); ``digest update`` then installs it.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from typing import Any

from workdigest.store.db import Store
from workdigest.version import __version__

_ANNOUNCED_KEY = "announced_mac_version"


def version_tuple(value: str) -> tuple[int, ...]:
    """Turn a version like 1.10.0+5 into (1, 10, 0), so versions compare numerically."""
    return tuple(
        int(p)
        for p in value.split("+", maxsplit=1)[0].split("-", maxsplit=1)[0].split(".")
        if p.isdigit()
    )


def latest_release(site_url: str, opener: Any = urllib.request.urlopen) -> dict[str, Any]:
    """The website's version.json (what the newest release is and where to get it)."""
    with opener(f"{site_url.rstrip('/')}/version.json", timeout=20) as response:
        data: dict[str, Any] = json.loads(response.read())
    return data


def mac_update_to_announce(
    store: Store,
    site_url: str,
    *,
    current: str = __version__,
    fetch: Callable[[str], dict[str, Any]] = latest_release,
) -> str | None:
    """The newer Mac version, the first time it is seen; None when up to date or already told."""
    if not site_url:
        return None
    try:
        latest = str(fetch(site_url).get("version", ""))
    except (OSError, ValueError):
        return None  # offline or a broken file: try again tomorrow
    if not latest or version_tuple(latest) <= version_tuple(current):
        return None
    if store.scalar("SELECT value FROM cloud_meta WHERE key = ?", (_ANNOUNCED_KEY,)) == latest:
        return None
    store.conn.execute(
        "INSERT INTO cloud_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (_ANNOUNCED_KEY, latest),
    )
    return latest


def announcement(version: str) -> tuple[str, str]:
    """(title, body) for the notification."""
    return (
        f"WorkDigest {version} for your Mac",
        "Run `digest update` in Terminal to install it (about a minute).",
    )
