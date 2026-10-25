"""Built-in secret-detection rules.

Each rule is a named regex. When a rule has a ``secret`` group, only that group is
replaced, so the surrounding context (``PGPASSWORD=``, ``Authorization: Bearer``) stays
readable while the value is removed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Rule:
    """A named secret pattern."""

    name: str
    pattern: re.Pattern[str]


def _rule(name: str, pattern: str, flags: int = 0) -> Rule:
    return Rule(name=name, pattern=re.compile(pattern, flags))


BUILTIN_RULES: tuple[Rule, ...] = (
    _rule(
        "private_key",
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
        re.DOTALL,
    ),
    _rule("aws_access_key", r"\b(?P<secret>(?:AKIA|ASIA|AGPA|AIDA|AROA)[A-Z0-9]{16})\b"),
    _rule(
        "aws_secret_key",
        r"(?i)aws_?secret_?(?:access_?)?key[\"']?\s*[:=]\s*[\"']?(?P<secret>[A-Za-z0-9/+=]{40})",
    ),
    _rule(
        "github_token", r"\b(?P<secret>(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,}))"
    ),
    _rule("atlassian_token", r"\b(?P<secret>ATAT[A-Za-z0-9_\-=]{30,})"),
    _rule("anthropic_key", r"\b(?P<secret>sk-ant-[A-Za-z0-9_\-]{20,})"),
    _rule("openai_key", r"\b(?P<secret>sk-(?:proj-)?[A-Za-z0-9_\-]{20,})"),
    _rule("cursor_key", r"\b(?P<secret>(?:key|crsr)_[a-f0-9]{40,})\b"),
    _rule("google_api_key", r"\b(?P<secret>AIza[0-9A-Za-z_\-]{35})"),
    _rule("slack_token", r"\b(?P<secret>xox[abprs]-[A-Za-z0-9\-]{10,})"),
    _rule("stripe_key", r"\b(?P<secret>(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,})"),
    _rule("supabase_key", r"\b(?P<secret>sb_(?:secret|publishable)_[A-Za-z0-9_\-]{20,})"),
    _rule(
        "jwt",
        r"\b(?P<secret>eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,})",
    ),
    _rule(
        "authorization_header",
        r"(?i)authorization[\"']?\s*[:=]\s*[\"']?(?:basic|bearer|token)\s+"
        r"(?P<secret>[A-Za-z0-9_\-\.=+/]{8,})",
    ),
    _rule("bearer_token", r"(?i)\bbearer\s+(?P<secret>[A-Za-z0-9_\-\.=+/]{16,})"),
    _rule(
        "url_credentials",
        r"(?i)\b[a-z][a-z0-9+\-.]*://[^\s:/@]+:(?P<secret>[^\s@/]+)@",
    ),
    _rule("cli_password_flag", r"(?i)(?:--password|--pass|--token|--secret)[= ](?P<secret>\S{3,})"),
    _rule(
        "password_assignment",
        r"(?im)(?:^|[\s\"'{,;(&?-])"
        r"(?:[A-Z0-9_]*?(?:PASSWORD|PASSWD|PASSPHRASE|PWD|SECRET|TOKEN|API_?KEY|"
        r"ACCESS_?KEY|PRIVATE_?KEY))"
        r"[\"']?\s*[:=]\s*[\"']?(?P<secret>[^\s\"',;&)]{3,})",
    ),
)

REDACTION_MARK = "[REDACTED:{name}]"
