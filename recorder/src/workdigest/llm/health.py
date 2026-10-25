"""Is each AI provider usable on this Mac right now?

A provider is *probed* with a tiny real prompt: installed is not enough, because an expired
login or an ended subscription only shows when a call is made. Results are kept locally
(to notice changes) and published to the cloud so the app can lock providers that do not
work. When the provider the user chose stops working, drafts fall back to facts only and the
user is told, on the Mac and in the app; when it works again they are told too.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal

from workdigest.clock import now_local
from workdigest.llm.base import LLMError, LLMProvider, LLMUnavailableError, Prompt
from workdigest.llm.cli_providers import ClaudeCliProvider, CursorCliProvider
from workdigest.store.db import Store

State = Literal["ok", "missing", "logged_out", "failing"]
PROVIDERS = {"cursor_cli": "Cursor", "claude_cli": "Claude Code"}
PROBE_TIMEOUT_S = 90
_META_KEY = "ai_capabilities"
_PROBE = Prompt(
    system="You are a health check. Reply with the single word OK.", user="Are you available?"
)
_LIMIT = re.compile(
    r"(?i)\b(quota|limit|usage|subscription|billing|payment|plan|credits?|402|429)\b"
)


@dataclass(frozen=True, slots=True)
class Capability:
    """One provider's state as last checked."""

    state: State
    detail: str
    checked_at: str

    @property
    def ok(self) -> bool:
        """Whether the provider answered the probe."""
        return self.state == "ok"


def _provider(name: str) -> LLMProvider:
    cls = CursorCliProvider if name == "cursor_cli" else ClaudeCliProvider
    return cls(timeout_s=PROBE_TIMEOUT_S)


def classify(error: LLMError) -> tuple[State, str]:
    """Map a provider error to a state and a short reason a person can act on."""
    message = str(error)
    if isinstance(error, LLMUnavailableError):
        if "not installed" in message:
            return "missing", "not installed"
        if "not logged in" in message:
            return "logged_out", "signed out; log in again on the Mac"
    if _LIMIT.search(message):
        return "failing", "subscription or usage limit reached"
    return "failing", message.split(": ", 1)[-1][:120]


def probe(
    name: str,
    *,
    factory: Callable[[str], LLMProvider] | None = None,
    clock: Callable[[], datetime] = now_local,
) -> Capability:
    """Check one provider with a real (tiny) call."""
    checked = clock().isoformat()
    provider = (factory or _provider)(name)  # looked up at call time (tests replace it)
    if getattr(provider, "binary_path", "present") is None:
        return Capability("missing", "not installed", checked)
    try:
        provider.complete(_PROBE)
    except LLMError as error:
        state, detail = classify(error)
        return Capability(state, detail, checked)
    return Capability("ok", "", checked)


@dataclass(frozen=True, slots=True)
class Change:
    """A provider changed between working and not working."""

    provider: str
    now_ok: bool
    capability: Capability

    def message(self) -> tuple[str, str]:
        """(title, body) for the notification."""
        label = PROVIDERS.get(self.provider, self.provider)
        if self.now_ok:
            return f"{label} works again", f"Drafts use {label} again."
        reason = self.capability.detail or "it stopped working"
        return (
            f"{label} stopped working",
            f"{label}: {reason}. Drafts are facts-only until it's fixed.",
        )


def load(store: Store) -> dict[str, Capability]:
    """The last results saved on this Mac."""
    raw = store.scalar("SELECT value FROM cloud_meta WHERE key = ?", (_META_KEY,))
    if not raw:
        return {}
    return {name: Capability(**value) for name, value in json.loads(raw).items()}


def save(store: Store, capabilities: dict[str, Capability]) -> None:
    """Remember the results (to notice the next change)."""
    store.conn.execute(
        "INSERT INTO cloud_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (_META_KEY, json.dumps({name: asdict(c) for name, c in capabilities.items()})),
    )


def check(
    store: Store,
    chosen: str,
    *,
    providers: tuple[str, ...] = tuple(PROVIDERS),
    prober: Callable[[str], Capability] = probe,
) -> tuple[dict[str, Capability], list[Change]]:
    """Probe every provider; report a change only for the one the user chose.

    The first check on a Mac reports a problem with the chosen provider (there is nothing to
    compare with, and the user should know); later checks report only transitions.
    """
    before = load(store)
    after = {name: prober(name) for name in providers}
    save(store, after)
    changes: list[Change] = []
    if chosen in after:
        was = before.get(chosen)
        now = after[chosen]
        if (was is None and not now.ok) or (was is not None and was.ok != now.ok):
            changes.append(Change(chosen, now.ok, now))
    return after, changes


def as_json(capabilities: dict[str, Capability]) -> dict[str, dict[str, str]]:
    """For the cloud `settings.capabilities` column."""
    return {name: asdict(c) for name, c in capabilities.items()}
