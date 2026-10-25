"""Read-only Bitbucket Cloud client (REST 2.0).

Verified 24 Oct 2026:
* app passwords are retired; API tokens use Basic auth (Atlassian email:token);
* listing and reading pull requests needs ``read:pullrequest:bitbucket``, because
  ``read:repository:bitbucket`` does not cover PRs. ``/user`` needs ``read:user:bitbucket``.

Not yet checked against a live response: the exact shape of PR ``activity`` entries.
Parsing is defensive and covered by contract tests built from the documented shapes.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from workdigest.clock import parse_iso
from workdigest.integrations.http import JsonClient

API = "https://api.bitbucket.org/2.0"
MAX_PAGES = 20
PR_STATES = ("OPEN", "MERGED", "DECLINED", "SUPERSEDED")


def repo_slug(web_url: str | None, workspace: str) -> str | None:
    """``https://bitbucket.org/<workspace>/<slug>`` → slug (only for the configured workspace)."""
    if not web_url:
        return None
    parsed = urlparse(web_url)
    parts = [p for p in parsed.path.split("/") if p]
    if parsed.netloc != "bitbucket.org" or len(parts) < 2 or parts[0].lower() != workspace.lower():
        return None
    return parts[1]


class BitbucketClient:
    """The few Bitbucket reads the digest needs."""

    def __init__(self, http: JsonClient, workspace: str) -> None:
        """Bind to one workspace."""
        self.http = http
        self.workspace = workspace

    def user(self) -> dict[str, Any]:
        """The token owner (uuid, display_name). Fails fast on bad credentials."""
        result: dict[str, Any] = self.http.get(f"{API}/user")
        return result

    def _paged(
        self, url: str, params: list[tuple[str, str]] | None = None
    ) -> Iterator[dict[str, Any]]:
        next_url: str | None = url
        first = True
        for _ in range(MAX_PAGES):
            if not next_url:
                return
            page = self.http.get(next_url, params if first else None)
            first = False
            yield from page.get("values", [])
            next_url = page.get("next")

    def pull_requests(
        self, slug: str, author_uuid: str, updated_since: datetime
    ) -> Iterator[dict[str, Any]]:
        """My PRs in ``slug`` updated since ``updated_since``, plus every open PR of mine."""
        stamp = updated_since.isoformat()
        query = f'author.uuid="{author_uuid}" AND (updated_on >= {stamp} OR state = "OPEN")'
        params = [
            ("q", query),
            ("pagelen", "50"),
            ("sort", "-updated_on"),
            *[("state", s) for s in PR_STATES],
        ]
        yield from self._paged(f"{API}/repositories/{self.workspace}/{slug}/pullrequests", params)

    def activity(self, slug: str, pr_id: int) -> list[dict[str, Any]]:
        """Approvals, updates and comments on one PR."""
        return list(
            self._paged(
                f"{API}/repositories/{self.workspace}/{slug}/pullrequests/{pr_id}/activity",
                [("pagelen", "50")],
            )
        )


def review_timeline(
    pr: dict[str, Any], activity: list[dict[str, Any]], my_uuid: str
) -> dict[str, Any]:
    """Opened, first approval by someone else, declined and merged times for one PR."""
    approvals: list[tuple[datetime, str]] = []
    declined: datetime | None = None
    merged: datetime | None = None
    for entry in activity:
        approval = entry.get("approval")
        if isinstance(approval, dict):
            user = approval.get("user") or {}
            when = parse_iso(str(approval.get("date") or ""))
            if when and user.get("uuid") != my_uuid:
                approvals.append(
                    (when, str(user.get("display_name") or user.get("nickname") or "?"))
                )
        update = entry.get("update")
        if isinstance(update, dict):
            when = parse_iso(str(update.get("date") or ""))
            state = update.get("state")
            if when and state == "DECLINED" and (declined is None or when < declined):
                declined = when
            if when and state == "MERGED" and (merged is None or when < merged):
                merged = when
    if pr.get("state") == "MERGED" and merged is None:
        merged = parse_iso(str(pr.get("updated_on") or ""))
    if pr.get("state") == "DECLINED" and declined is None:
        declined = parse_iso(str(pr.get("updated_on") or ""))
    approvals.sort()
    return {
        "created_at": parse_iso(str(pr.get("created_on") or "")),
        "updated_at": parse_iso(str(pr.get("updated_on") or "")),
        "first_approved_at": approvals[0][0] if approvals else None,
        "approved_by": sorted({name for _, name in approvals}),
        "declined_at": declined,
        "merged_at": merged,
    }


def human_duration(start: datetime, end: datetime) -> str:
    """``3d 4h``, ``16h 12m`` or ``25m``."""
    minutes = max(int((end - start).total_seconds() // 60), 0)
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"
