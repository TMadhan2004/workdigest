"""Redaction corpus and property tests."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from workdigest.config import RedactionSettings
from workdigest.redact.redactor import Redactor, shannon_entropy

DEFAULTS = RedactionSettings()


@pytest.fixture
def redactor() -> Redactor:
    return Redactor(
        known_secrets=["kc-SuperSecretValue-123"],
        sensitive_path_globs=DEFAULTS.sensitive_path_globs,
        safe_path_globs=DEFAULTS.safe_path_globs,
    )


# (case id, input, secret that must disappear, rule expected to fire)
SECRET_CORPUS = [
    (
        "aws-access",
        "export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE",
        "AKIAIOSFODNN7EXAMPLE",
        "aws_access_key",
    ),
    (
        "aws-secret",
        "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "aws_secret_key",
    ),
    (
        "github",
        "token ghp_abcdefghijklmnopqrstuvwxyz0123456789AB",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789AB",
        "github_token",
    ),
    (
        "atlassian",
        "JIRA_TOKEN=ATATT3xFfGF0abcdefghijklmnopqrstuvwxyz0123456789",
        "ATATT3xFfGF0abcdefghijklmnopqrstuvwxyz0123456789",
        "atlassian_token",
    ),
    (
        "anthropic",
        "key sk-ant-api03-abcdefghijklmnopqrstuvwx",
        "sk-ant-api03-abcdefghijklmnopqrstuvwx",
        "anthropic_key",
    ),
    (
        "jwt",
        "auth eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0",
        "jwt",
    ),
    (
        "pgpassword",
        "PGPASSWORD=postgres psql -h localhost -p 5433",
        "postgres psql",
        "password_assignment",
    ),
    ("json-password", '{"password": "hunter2!", "user": "a"}', "hunter2!", "password_assignment"),
    (
        "url-creds",
        "postgres://admin:s3cr3tPass@db.internal:5432/app",
        "s3cr3tPass",
        "url_credentials",
    ),
    (
        "bearer",
        "curl -H 'Authorization: Bearer abcDEF123456ghiJKL789' https://x",
        "abcDEF123456ghiJKL789",
        "authorization_header",
    ),
    ("cli-flag", "mytool login --password=Pa55w0rd", "Pa55w0rd", "cli_password_flag"),
    (
        "pem",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----",
        "MIIEowIBAAKCAQEA",
        "private_key",
    ),
    ("slack", "xoxb-1234567890-abcdefghij", "xoxb-1234567890-abcdefghij", "slack_token"),
    (
        "known",
        "the value kc-SuperSecretValue-123 leaked",
        "kc-SuperSecretValue-123",
        "known_secret",
    ),
    (
        "entropy",
        "secret blob Zx9qL2mN8vB4cR7tY1wE5uI3oP6aS0dF here",
        "Zx9qL2mN8vB4cR7tY1wE5uI3oP6aS0dF",
        "high_entropy",
    ),
]


@pytest.mark.parametrize(
    ("case", "text", "secret", "rule"), SECRET_CORPUS, ids=[c[0] for c in SECRET_CORPUS]
)
def test_secret_corpus_is_redacted(
    redactor: Redactor, case: str, text: str, secret: str, rule: str
) -> None:
    """TC-RED-001: Every known secret shape is removed and the rule that fired is reported.

    Given: a corpus of real-world secret formats (AWS, GitHub, Atlassian, Anthropic, JWT,
           PGPASSWORD, JSON password, URL credentials, bearer header, CLI flag, PEM, Slack,
           Keychain-known value, high-entropy blob).
    When: each is redacted.
    Then: the secret text is gone and the expected rule name is in rules_hit.
    """
    result = redactor.redact_text(text)
    assert secret.split(maxsplit=1)[0] not in result.value, case
    assert rule in result.rules_hit, (case, result.rules_hit)


EVIDENCE_CORPUS = [
    "Tests  644 passed (644)",
    "commit 3f9c2b1e8d7a6f5e4d3c2b1a0f9e8d7c6b5a4f3e on feat/billing-migrator",
    "EIT-8403 and CA-0214 updated",
    "id 5239f57a-a0a0-48a2-92b8-9b4ee0e4e1b5",
    "--- PASS: TestOTPCannotBeReusedConcurrently (0.01s)",
    "https://bitbucket.org/ei-india-admin/ei-factory-mcp/pull-requests/41",
    "/Users/Arjun.T/Desktop/microservices-redesign/billing-service/internal/billing/service.go",
    "ok  \tgithub.com/ei/identity/internal/otp\t0.412s",
    "Executed 966 of 966 SUCCESS",
    "context_tokens: 182340",
]


@pytest.mark.parametrize("text", EVIDENCE_CORPUS)
def test_evidence_is_not_redacted(redactor: Redactor, text: str) -> None:
    """TC-RED-002: Evidence the digest depends on survives redaction unchanged.

    Given: test summaries, git SHAs, ticket keys, UUIDs, Go test names, PR URLs, file paths
           and token counts.
    When: redacted.
    Then: the text is unchanged and no rule fires.
    """
    result = redactor.redact_text(text)
    assert result.value == text
    assert not result.applied


def test_nested_structures_are_walked(redactor: Redactor) -> None:
    """TC-RED-003: Secrets nested in dicts and lists are redacted and the others kept.

    Given: a payload with a secret two levels deep, plus non-string values.
    When: redact() runs.
    Then: only the secret string changes; numbers, booleans and None are preserved.
    """
    payload = {
        "tool_input": {"env": ["X=1", "GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789AB"]},
        "n": 3,
        "ok": True,
        "z": None,
    }
    result = redactor.redact(payload)
    assert "ghp_" not in str(result.value)
    assert result.value["n"] == 3
    assert result.value["ok"] is True
    assert result.value["z"] is None
    assert result.value["tool_input"]["env"][0] == "X=1"


@pytest.mark.parametrize(
    ("path", "sensitive"),
    [
        (".env", True),
        ("/repo/.env.production", True),
        ("config/app.env", True),
        ("certs/server.pem", True),
        ("~/.ssh/id_ed25519", True),
        (".env.example", False),
        ("src/environment.ts", False),
        ("README.md", False),
    ],
)
def test_sensitive_paths(redactor: Redactor, path: str, sensitive: bool) -> None:
    """TC-RED-004: .env files, keys and certificates are sensitive; .env.example is not.

    Given: a mix of secret-bearing and ordinary file paths.
    When: is_sensitive_path() runs.
    Then: only the secret-bearing files are flagged.
    """
    assert redactor.is_sensitive_path(path) is sensitive


def test_command_touching_env_file_is_detected(redactor: Redactor) -> None:
    """TC-RED-005: Shell commands that read sensitive files are detected.

    Given: `cat .env`, `source ./.env.local && npm start` and `cat .env.example`.
    When: mentions_sensitive_path() runs.
    Then: the first two are flagged and the example file is not.
    """
    assert redactor.mentions_sensitive_path("cat .env")
    assert redactor.mentions_sensitive_path("source ./.env.local && npm start")
    assert not redactor.mentions_sensitive_path("cat .env.example")


def test_custom_pattern_with_secret_group() -> None:
    """TC-RED-006: User-supplied patterns work, and a `secret` group limits what is replaced.

    Given: a custom pattern `INTERNAL-(?P<secret>\\d{6})`.
    When: text containing INTERNAL-123456 is redacted.
    Then: the digits are replaced, the prefix is kept, and the rule is custom_0.
    """
    redactor = Redactor(extra_patterns=[r"INTERNAL-(?P<secret>\d{6})"])
    result = redactor.redact_text("id INTERNAL-123456 ok")
    assert result.value == "id INTERNAL-[REDACTED:custom_0] ok"
    assert result.rules_hit == {"custom_0"}


def test_entropy_helper() -> None:
    """TC-RED-007: Shannon entropy separates random tokens from words.

    Given: an empty string, a repeated character and a random-looking token.
    When: entropy is computed.
    Then: it is 0 for the first two and above 4 bits/char for the token.
    """
    assert shannon_entropy("") == 0.0
    assert shannon_entropy("aaaa") == 0.0
    assert shannon_entropy("Zx9qL2mN8vB4cR7tY1wE5uI3oP6aS0dF") > 4.0


@settings(max_examples=200, deadline=None)
@given(st.text(max_size=300))
def test_redaction_is_idempotent(text: str) -> None:
    """TC-RED-008: Redacting already-redacted text changes nothing (property test).

    Given: arbitrary Unicode text.
    When: it is redacted twice.
    Then: the second pass returns the first pass's output unchanged.
    """
    redactor = Redactor()
    once = redactor.redact_text(text).value
    assert redactor.redact_text(once).value == once


@settings(max_examples=200, deadline=None)
@given(prefix=st.text(max_size=80), suffix=st.text(max_size=80))
def test_embedded_github_token_never_survives(prefix: str, suffix: str) -> None:
    """TC-RED-009: A GitHub token is removed whatever text surrounds it (property test).

    Given: a fixed token embedded between arbitrary prefixes and suffixes.
    When: redacted.
    Then: the token body never appears in the output.
    """
    token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    out = Redactor().redact_text(f"{prefix} {token} {suffix}").value
    assert "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8" not in out
