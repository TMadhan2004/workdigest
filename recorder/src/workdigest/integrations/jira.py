"""Read-only Jira Cloud client (REST v3 through the API gateway, for scoped tokens).

Verified 24 Oct 2026:
* scoped API tokens use Basic auth (email:token) against
  ``https://api.atlassian.com/ex/jira/{cloudId}``, not the site URL;
* JQL search is ``POST /rest/api/3/search/jql`` with ``nextPageToken`` paging. The legacy
  ``/rest/api/3/search`` returns 410 Gone;
* a scoped token belongs to one app, so Jira and Bitbucket need separate tokens.

Community reports say ``search/jql`` paging can loop. Paging stops when a token repeats or
after ``MAX_PAGES`` pages.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from workdigest.integrations.http import HttpError, JsonClient, JsonHttpClient

GATEWAY = "https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3"
MAX_PAGES = 20
ISSUE_FIELDS = ["summary", "status", "issuetype", "duedate", "parent", "assignee", "updated"]


_CLOUD_IDS: dict[str, str] = {}


def resolve_cloud_id(site: str, *, opener: Callable[..., Any] | None = None) -> str:
    """The Jira site's cloud id (needed by the API gateway), from its public tenant_info.

    Cached per process: it never changes for a site.
    """
    site = site.rstrip("/")
    if site not in _CLOUD_IDS:
        payload = JsonHttpClient(dict, opener=opener, retries=2).get(f"{site}/_edge/tenant_info")
        cloud_id = str((payload or {}).get("cloudId") or "")
        if not cloud_id:
            raise HttpError(0, f"{site}/_edge/tenant_info", "no cloudId in the response")
        _CLOUD_IDS[site] = cloud_id
    return _CLOUD_IDS[site]


class JiraClient:
    """The few Jira reads the digest needs."""

    def __init__(self, http: JsonClient, cloud_id: str, site: str) -> None:
        """Bind to one Jira Cloud site."""
        self.http = http
        self.base = GATEWAY.format(cloud_id=cloud_id)
        self.site = site.rstrip("/")

    def browse_url(self, key: str) -> str:
        """Human link for a ticket."""
        return f"{self.site}/browse/{key}"

    def myself(self) -> dict[str, Any]:
        """The token owner (accountId, displayName). Fails fast on bad credentials."""
        result: dict[str, Any] = self.http.get(f"{self.base}/myself")
        return result

    def search(
        self, jql: str, fields: list[str] | None = None, page_size: int = 50
    ) -> Iterator[dict[str, Any]]:
        """Yield issues matching ``jql`` (bounded paging, see module docstring)."""
        token: str | None = None
        seen_tokens: set[str] = set()
        for _ in range(MAX_PAGES):
            body: dict[str, Any] = {
                "jql": jql,
                "fields": fields or ISSUE_FIELDS,
                "maxResults": page_size,
            }
            if token:
                body["nextPageToken"] = token
            page = self.http.post(f"{self.base}/search/jql", body)
            yield from page.get("issues", [])
            token = page.get("nextPageToken")
            if not token or page.get("isLast") or token in seen_tokens:
                return
            seen_tokens.add(token)

    def issue(self, key: str) -> dict[str, Any] | None:
        """One issue, or None when it does not exist or is not visible."""
        try:
            result: dict[str, Any] = self.http.get(
                f"{self.base}/issue/{key}", {"fields": ",".join(ISSUE_FIELDS)}
            )
        except HttpError as exc:
            if exc.status in {404, 400}:
                return None
            raise
        return result

    def comments(self, key: str) -> list[dict[str, Any]]:
        """Latest comments first (one page is enough for a single day)."""
        page = self.http.get(
            f"{self.base}/issue/{key}/comment", {"orderBy": "-created", "maxResults": 50}
        )
        comments: list[dict[str, Any]] = page.get("comments", [])
        return comments

    def changelog(self, key: str) -> list[dict[str, Any]]:
        """Change history entries (most recent pages only)."""
        entries: list[dict[str, Any]] = []
        start = 0
        for _ in range(MAX_PAGES):
            page = self.http.get(
                f"{self.base}/issue/{key}/changelog", {"startAt": start, "maxResults": 100}
            )
            values = page.get("values", [])
            entries.extend(values)
            if page.get("isLast", True) or not values:
                break
            start += len(values)
        return entries


def adf_text(node: Any, limit: int = 400) -> str:
    """Plain text from an Atlassian Document Format comment body."""
    parts: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
            for child in item.get("content", []) or []:
                walk(child)
            if item.get("type") in {"paragraph", "listItem", "heading"}:
                parts.append(" ")
        elif isinstance(item, list):
            for child in item:
                walk(child)
        elif isinstance(item, str):
            parts.append(item)

    walk(node)
    text = " ".join("".join(parts).split())
    return text if len(text) <= limit else text[:limit] + "…"
