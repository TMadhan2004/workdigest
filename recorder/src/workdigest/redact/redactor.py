"""Secret redaction applied to every captured value before it is stored or sent.

Redaction runs on the Mac, at ingest time, before anything reaches SQLite. The sync
layer runs it again before upload, as defence in depth. It is deliberately aggressive:
a false positive costs a word in the digest, but a false negative leaks a credential.
"""

from __future__ import annotations

import fnmatch
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any

from workdigest.redact.rules import BUILTIN_RULES, REDACTION_MARK, Rule

# Long opaque strings that look random. Hex-only strings (git SHAs, hashes) and UUIDs
# are evidence, not secrets, so they are excluded explicitly.
_CANDIDATE = re.compile(r"[A-Za-z0-9_\-+/=]{32,}")
_HEX_ONLY = re.compile(r"^[0-9a-fA-F]+$")
_UUID_LIKE = re.compile(r"^[0-9a-fA-F\-]{36}$")
_PATH_LIKE = re.compile(r"^[\w\-./]+$")
_ENTROPY_THRESHOLD = 4.0
_MIN_KNOWN_SECRET_LEN = 6


def shannon_entropy(value: str) -> float:
    """Bits of entropy per character of ``value``."""
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for char in value:
        counts[char] = counts.get(char, 0) + 1
    total = len(value)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def _looks_random(token: str) -> bool:
    if _HEX_ONLY.match(token) or _UUID_LIKE.match(token):
        return False
    has_upper = any(c.isupper() for c in token)
    has_lower = any(c.islower() for c in token)
    has_digit = any(c.isdigit() for c in token)
    if not (has_upper and has_lower and has_digit):
        return False
    # File paths and identifiers made of words are not secrets.
    if _PATH_LIKE.match(token) and token.count("/") >= 2:
        return False
    return shannon_entropy(token) >= _ENTROPY_THRESHOLD


@dataclass(slots=True)
class RedactionResult:
    """Redacted value plus the names of the rules that fired."""

    value: Any
    rules_hit: set[str] = field(default_factory=set)

    @property
    def applied(self) -> bool:
        """Whether anything was removed."""
        return bool(self.rules_hit)


class Redactor:
    """Removes secrets from strings and nested JSON-like structures."""

    def __init__(
        self,
        extra_patterns: Iterable[str] = (),
        known_secrets: Iterable[str] = (),
        sensitive_path_globs: Iterable[str] = (),
        safe_path_globs: Iterable[str] = (),
    ) -> None:
        """Build a redactor.

        Args:
            extra_patterns: User regexes; a ``secret`` group, if present, limits the
                replacement to that group.
            known_secrets: Exact values that must never appear (e.g. Keychain items).
            sensitive_path_globs: File names whose contents must never be stored.
            safe_path_globs: Exceptions to ``sensitive_path_globs`` (``.env.example``).
        """
        self._rules: list[Rule] = list(BUILTIN_RULES)
        for index, pattern in enumerate(extra_patterns):
            self._rules.append(Rule(name=f"custom_{index}", pattern=re.compile(pattern)))
        self._known = sorted(
            {s for s in known_secrets if len(s) >= _MIN_KNOWN_SECRET_LEN}, key=len, reverse=True
        )
        self._sensitive_globs = tuple(sensitive_path_globs)
        self._safe_globs = tuple(safe_path_globs)

    # ------------------------------------------------------------------ paths
    def is_sensitive_path(self, path: str) -> bool:
        """Return True when the file's contents must never be captured."""
        name = PurePath(path).name
        if any(fnmatch.fnmatch(name, glob) for glob in self._safe_globs):
            return False
        return any(fnmatch.fnmatch(name, glob) for glob in self._sensitive_globs)

    def mentions_sensitive_path(self, text: str) -> bool:
        """Return True when a command line touches a sensitive file (``cat .env``)."""
        return any(
            self.is_sensitive_path(token) for token in re.split(r"[\s'\"<>|;&=]+", text) if token
        )

    # ---------------------------------------------------------------- strings
    def redact_text(self, text: str) -> RedactionResult:
        """Redact one string."""
        hits: set[str] = set()
        out = text
        for secret in self._known:
            if secret in out:
                out = out.replace(secret, REDACTION_MARK.format(name="known_secret"))
                hits.add("known_secret")
        for rule in self._rules:
            out, fired = _apply_rule(rule, out)
            if fired:
                hits.add(rule.name)
        out, fired = _apply_entropy(out)
        if fired:
            hits.add("high_entropy")
        return RedactionResult(value=out, rules_hit=hits)

    # -------------------------------------------------------------- structures
    def redact(self, value: Any) -> RedactionResult:
        """Recursively redact strings inside dicts, lists and tuples."""
        hits: set[str] = set()
        redacted = self._walk(value, hits)
        return RedactionResult(value=redacted, rules_hit=hits)

    def _walk(self, value: Any, hits: set[str]) -> Any:
        if isinstance(value, str):
            result = self.redact_text(value)
            hits |= result.rules_hit
            return result.value
        if isinstance(value, Mapping):
            return {key: self._walk(item, hits) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [self._walk(item, hits) for item in value]
        return value


def _apply_rule(rule: Rule, text: str) -> tuple[str, bool]:
    fired = False
    mark = REDACTION_MARK.format(name=rule.name)

    def replace(match: re.Match[str]) -> str:
        nonlocal fired
        if "secret" in match.re.groupindex and match.group("secret") is not None:
            secret = match.group("secret")
            if secret.startswith("[REDACTED:"):
                return match.group(0)
            fired = True
            start, end = match.span("secret")
            offset = match.start()
            whole = match.group(0)
            return whole[: start - offset] + mark + whole[end - offset :]
        fired = True
        return mark

    return rule.pattern.sub(replace, text), fired


def _apply_entropy(text: str) -> tuple[str, bool]:
    fired = False

    def replace(match: re.Match[str]) -> str:
        nonlocal fired
        token = match.group(0)
        if _looks_random(token):
            fired = True
            return REDACTION_MARK.format(name="high_entropy")
        return token

    return _CANDIDATE.sub(replace, text), fired
