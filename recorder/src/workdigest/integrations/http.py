"""Minimal JSON-over-HTTPS client (stdlib) with Basic auth, retries and safe errors.

Only GET and POST are used, and only to read. The token is never included in exceptions,
logs or ``repr``.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol

RETRY_STATUSES = {429, 500, 502, 503, 504}
USER_AGENT = "WorkDigest/0.1 (read-only digest recorder)"


class HttpError(RuntimeError):
    """A request failed; carries the status and a short body excerpt."""

    def __init__(self, status: int, url: str, body: str) -> None:
        """Build the error without any credential material."""
        super().__init__(f"HTTP {status} for {url}: {body[:300]}")
        self.status = status
        self.url = url
        self.body = body


class AuthError(HttpError):
    """401/403: missing, expired or under-scoped token."""


class JsonClient(Protocol):
    """What the Jira and Bitbucket clients need."""

    def get(self, url: str, params: dict[str, Any] | list[tuple[str, str]] | None = None) -> Any:
        """GET ``url`` and decode JSON."""

    def post(self, url: str, body: dict[str, Any]) -> Any:
        """POST JSON to ``url`` (Jira uses it only for its read-only JQL search)."""


class JsonHttpClient:
    """urllib-based JSON client. Auth headers come from ``auth_headers()`` on every request."""

    def __init__(
        self,
        auth_headers: Callable[[], dict[str, str]],
        *,
        timeout_s: float = 20,
        retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        """``auth_headers`` is called per request so tokens can refresh between calls."""
        self._auth_headers = auth_headers
        self._timeout = timeout_s
        self._retries = retries
        self._sleep = sleep
        self._open = opener or urllib.request.urlopen

    def __repr__(self) -> str:
        """Never show credentials."""
        return f"{type(self).__name__}(<redacted>)"

    def get(
        self,
        url: str,
        params: dict[str, Any] | list[tuple[str, str]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """GET with query parameters."""
        return self._request("GET", _with_query(url, params), None, headers)

    def post(self, url: str, body: Any, headers: dict[str, str] | None = None) -> Any:
        """POST a JSON body."""
        return self._request("POST", url, json.dumps(body).encode(), headers)

    def patch(
        self,
        url: str,
        body: Any,
        params: dict[str, Any] | list[tuple[str, str]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """PATCH a JSON body (Supabase row updates)."""
        return self._request("PATCH", _with_query(url, params), json.dumps(body).encode(), headers)

    def _request(
        self, method: str, url: str, data: bytes | None, headers: dict[str, str] | None
    ) -> Any:
        if not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise ValueError("only https URLs are allowed (plain http only for a local Supabase)")
        attempt = 0
        while True:
            request = urllib.request.Request(url, data=data, method=method)  # noqa: S310 - scheme checked above
            for name, value in {**self._auth_headers(), **(headers or {})}.items():
                request.add_header(name, value)
            request.add_header("Accept", "application/json")
            request.add_header("User-Agent", USER_AGENT)
            if data is not None:
                request.add_header("Content-Type", "application/json")
            try:
                with self._open(request, timeout=self._timeout) as response:
                    payload = response.read().decode("utf-8")
                    return json.loads(payload) if payload else None
            except urllib.error.HTTPError as exc:
                try:
                    body = exc.read().decode("utf-8", "replace") if exc.fp else ""
                finally:
                    exc.close()  # the error holds the open connection; never leak it on retry
                if exc.code in RETRY_STATUSES and attempt < self._retries:
                    attempt += 1
                    self._sleep(_retry_after(exc.headers.get("Retry-After"), attempt))
                    continue
                error = AuthError if exc.code in {401, 403} else HttpError
                raise error(exc.code, _safe_url(url), body) from None
            except urllib.error.URLError as exc:
                if attempt < self._retries:
                    attempt += 1
                    self._sleep(2.0**attempt)
                    continue
                raise HttpError(0, _safe_url(url), str(exc.reason)) from None


class BasicAuthClient(JsonHttpClient):
    """Basic auth (Atlassian email:token)."""

    def __init__(
        self,
        email: str,
        token: str,
        *,
        timeout_s: float = 20,
        retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        """Create a client for one Atlassian account."""
        header = {
            "Authorization": "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()
        }
        super().__init__(
            lambda: header, timeout_s=timeout_s, retries=retries, sleep=sleep, opener=opener
        )


def _with_query(url: str, params: dict[str, Any] | list[tuple[str, str]] | None) -> str:
    if not params:
        return url
    return f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(params, doseq=True)}"


def _retry_after(header: str | None, attempt: int) -> float:
    if header and header.strip().isdigit():
        return min(float(header.strip()), 60.0)
    return min(2.0**attempt, 30.0)


def _safe_url(url: str) -> str:
    """URL without its query string (query values can carry user data)."""
    return url.split("?", 1)[0]
