"""Credentials in the macOS Keychain (never in files, config, logs or the database).

Tokens are written with ``digest secrets set <name>``, which reads them with hidden input.
Every stored secret is also handed to the redactor as a known value, so one can never end
up in captured evidence, even if it appears in a command line or tool output.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Protocol

import keyring
from keyring.errors import KeyringError

# A separate Keychain service for trying a build without touching your real tokens.
SERVICE = os.environ.get("WORKDIGEST_KEYCHAIN_SERVICE", "WorkDigest")
KNOWN_SECRETS = ("jira-token", "bitbucket-token", "supabase-db-password", "supabase-refresh-token")


class SecretBackend(Protocol):
    """Subset of the keyring API we use (lets tests swap in a fake)."""

    def get_password(self, service_name: str, username: str) -> str | None:
        """Read a secret."""

    def set_password(self, service_name: str, username: str, password: str) -> None:
        """Store a secret."""

    def delete_password(self, service_name: str, username: str) -> None:
        """Remove a secret."""


class SecretStore:
    """Named secrets under the WorkDigest Keychain service."""

    def __init__(self, backend: SecretBackend | None = None) -> None:
        """Use the system keyring (macOS Keychain) unless a backend is given."""
        self._backend: SecretBackend = backend or keyring.get_keyring()

    def get(self, name: str) -> str | None:
        """Return the secret, or None when missing or the keychain is locked."""
        try:
            return self._backend.get_password(SERVICE, name)
        except KeyringError:
            return None

    def set(self, name: str, value: str) -> None:
        """Store ``value`` (whitespace stripped). Empty values are rejected."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("refusing to store an empty secret")
        self._backend.set_password(SERVICE, name, cleaned)

    def delete(self, name: str) -> bool:
        """Remove a secret; True if it existed."""
        if self.get(name) is None:
            return False
        self._backend.delete_password(SERVICE, name)
        return True

    def known_values(self, names: Iterable[str] = KNOWN_SECRETS) -> tuple[str, ...]:
        """All stored secret values, for the redactor."""
        return tuple(value for name in names if (value := self.get(name)))
