"""Supabase access as the signed-in user: GoTrue auth plus a PostgREST table client.

* Sign-in uses email and password once. Only the **refresh token** is kept, in the Keychain,
  and it is rotated on every refresh, as Supabase does.
* Every request carries the public publishable key and the user's short-lived access token,
  so row-level security applies. The service-role key is never used.
"""

from __future__ import annotations

import fcntl
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from workdigest.integrations.http import AuthError, HttpError, JsonHttpClient
from workdigest.paths import Paths
from workdigest.secrets import SecretStore

REFRESH_SECRET = "supabase-refresh-token"  # noqa: S105 - the Keychain entry name, not a secret
EXPIRY_MARGIN_S = 60


class CloudError(RuntimeError):
    """Cloud sync is not configured or the session cannot be refreshed."""


class Session(BaseModel):
    """A Supabase auth session (the access token is kept in memory only)."""

    access_token: str
    refresh_token: str
    expires_at: float
    user_id: str
    email: str | None = None


def _session(payload: dict[str, Any], clock: Callable[[], float]) -> Session:
    user = payload.get("user") or {}
    expires_at = payload.get("expires_at") or (clock() + float(payload.get("expires_in") or 3600))
    return Session(
        access_token=payload["access_token"],
        refresh_token=payload["refresh_token"],
        expires_at=float(expires_at),
        user_id=str(user.get("id")),
        email=user.get("email"),
    )


class CloudAuth:
    """Email/password sign-in and refresh-token rotation."""

    def __init__(
        self,
        url: str,
        publishable_key: str,
        *,
        secrets: SecretStore | None = None,
        opener: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.time,
        lock_path: Path | None = None,
    ) -> None:
        """Bind to one Supabase project. ``lock_path`` serializes refreshes across processes."""
        if not url or not publishable_key:
            raise CloudError(
                "cloud.url and cloud.publishable_key are not set; run `digest cloud setup`"
            )
        self.url = url.rstrip("/")
        self._secrets = secrets or SecretStore()
        self._clock = clock
        self._http = JsonHttpClient(lambda: {"apikey": publishable_key}, opener=opener, retries=2)
        self._session: Session | None = None
        self._lock_path = lock_path or Paths.resolve().home / "auth.lock"
        # The heartbeat thread and the worker share this session; a refresh token is single-use,
        # so two concurrent refreshes would revoke the session.
        self._lock = threading.Lock()

    def sign_up(self, email: str, password: str) -> dict[str, Any]:
        """Create the account (Supabase may email a confirmation link first)."""
        result: dict[str, Any] = self._http.post(
            f"{self.url}/auth/v1/signup", {"email": email, "password": password}
        )
        if result.get("access_token"):
            self._store(_session(result, self._clock))
        return result

    def sign_in(self, email: str, password: str) -> Session:
        """Exchange email and password for a session; only the refresh token is persisted."""
        payload = self._http.post(
            f"{self.url}/auth/v1/token?grant_type=password", {"email": email, "password": password}
        )
        session = _session(payload, self._clock)
        self._store(session)
        return session

    def sign_out(self) -> None:
        """Forget the stored refresh token."""
        self._secrets.delete(REFRESH_SECRET)
        self._session = None

    def session(self) -> Session:
        """A valid session, refreshing (and rotating the stored refresh token) when needed."""
        with self._lock:
            return self._session_locked()

    def _session_locked(self) -> Session:
        if self._session and self._session.expires_at - EXPIRY_MARGIN_S > self._clock():
            return self._session
        # Refresh tokens are single-use and shared by every process on this Mac (the daemon, the
        # CLI, one-off scripts). The Keychain always holds the newest one, so read it there, under
        # a file lock: refreshing with a token another process already used makes Supabase revoke
        # the whole session.
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                refresh = self._secrets.get(REFRESH_SECRET) or (
                    self._session.refresh_token if self._session else None
                )
                if not refresh:
                    raise CloudError("not signed in; run `digest cloud login`")
                try:
                    payload = self._http.post(
                        f"{self.url}/auth/v1/token?grant_type=refresh_token",
                        {"refresh_token": refresh},
                    )
                except HttpError as exc:
                    if isinstance(exc, AuthError) or exc.status == 400:
                        raise CloudError(
                            "the Supabase session on this Mac is no longer valid; "
                            "run `digest cloud login` again"
                        ) from exc
                    raise
                session = _session(payload, self._clock)
                self._store(session)
                return session
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    def _store(self, session: Session) -> None:
        self._session = session
        self._secrets.set(REFRESH_SECRET, session.refresh_token)


class Rest:
    """PostgREST table access as the signed-in user."""

    def __init__(
        self, auth: CloudAuth, publishable_key: str, *, opener: Callable[..., Any] | None = None
    ) -> None:
        """Create a client; the access token is fetched (and refreshed) per request."""
        self.auth = auth
        self.base = f"{auth.url}/rest/v1"
        self._http = JsonHttpClient(
            lambda: {
                "apikey": publishable_key,
                "Authorization": f"Bearer {auth.session().access_token}",
            },
            opener=opener,
        )

    @property
    def user_id(self) -> str:
        """The signed-in user's id."""
        return self.auth.session().user_id

    def select(self, table: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        """Rows matching PostgREST filters, e.g. ``{"status": "eq.queued", "order": "created_at"}``."""
        rows: list[dict[str, Any]] = self._http.get(f"{self.base}/{table}", params or {}) or []
        return rows

    def upsert(self, table: str, rows: list[dict[str, Any]], on_conflict: str) -> None:
        """Insert or update rows by primary key."""
        if rows:
            self._http.post(
                f"{self.base}/{table}?on_conflict={on_conflict}",
                rows,
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            )

    def insert(self, table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Insert rows and return them."""
        result: list[dict[str, Any]] = (
            self._http.post(
                f"{self.base}/{table}", rows, headers={"Prefer": "return=representation"}
            )
            or []
        )
        return result

    def update(
        self, table: str, filters: dict[str, str], values: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Update matching rows and return the ones changed (empty when none matched)."""
        result: list[dict[str, Any]] = (
            self._http.patch(
                f"{self.base}/{table}", values, filters, headers={"Prefer": "return=representation"}
            )
            or []
        )
        return result
