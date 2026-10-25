"""Hook payload normalization, source detection, repo attribution and ticket keys."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from tests.conftest import IST, claude_payload, cursor_payload, git
from workdigest.ingest.context import RepoResolver, TicketMatcher
from workdigest.ingest.normalize import (
    EventFactory,
    UnsupportedPayloadError,
    detect_source,
    effective_dir,
)
from workdigest.models import Kind, Source

T0 = datetime(2026, 10, 23, 11, 0, tzinfo=IST)


@pytest.mark.parametrize(
    ("payload", "hint", "expected"),
    [
        (claude_payload("Stop"), "claude_code", Source.CLAUDE_CODE),
        (cursor_payload("stop"), "cursor", Source.CURSOR),
        (cursor_payload("stop"), "claude_code", Source.CURSOR),
        ({"foo": 1}, "cursor", Source.CURSOR),
    ],
)
def test_detect_source(payload: dict[str, object], hint: str, expected: Source) -> None:
    """TC-NRM-001: The source comes from payload fields first, then the configured hint.

    Given: Claude payloads, Cursor payloads (including ones delivered via a Claude hook) and
           a bare payload.
    When: detect_source() runs.
    Then: Cursor fields always win, and the hint is used only as a fallback.
    """
    assert detect_source(payload, hint) is expected


def test_detect_source_rejects_unknown() -> None:
    """TC-NRM-002: A payload with no recognisable fields and no hint is rejected.

    Given: {"foo": 1} with an unknown hint.
    When: detect_source() runs.
    Then: UnsupportedPayloadError is raised.
    """
    with pytest.raises(UnsupportedPayloadError):
        detect_source({"foo": 1}, "unknown")


@pytest.mark.parametrize(
    ("event", "kind"),
    [
        ("SessionStart", Kind.SESSION_START),
        ("UserPromptSubmit", Kind.PROMPT),
        ("PostToolUse", Kind.TOOL_USE),
        ("Stop", Kind.STOP),
        ("SubagentStop", Kind.SUBAGENT_STOP),
        ("SessionEnd", Kind.SESSION_END),
    ],
)
def test_claude_event_mapping(factory: EventFactory, event: str, kind: Kind) -> None:
    """TC-NRM-003: Every subscribed Claude Code hook event maps to a normalized kind.

    Given: a payload for each Claude Code event we subscribe to.
    When: normalized.
    Then: the kind matches and the session key is claude:<session_id>.
    """
    built = factory.from_hook(
        claude_payload(event, tool_name="Read", tool_input={}), "claude_code", T0
    )
    assert built.kind is kind
    assert built.session_key == "claude:sess-A"


@pytest.mark.parametrize(
    ("event", "kind", "extra"),
    [
        ("sessionStart", Kind.SESSION_START, {"session_id": "x", "composer_mode": "agent"}),
        (
            "beforeSubmitPrompt",
            Kind.PROMPT,
            {"prompt": "hello", "attachments": [{"type": "file", "file_path": "/a.ts"}]},
        ),
        (
            "postToolUse",
            Kind.TOOL_USE,
            {"tool_name": "Read", "tool_input": {"path": "/a.ts"}, "tool_use_id": "t1"},
        ),
        ("afterShellExecution", Kind.SHELL, {"command": "ls", "output": "a\nb", "duration": 3}),
        (
            "afterFileEdit",
            Kind.FILE_EDIT,
            {"file_path": "/a.ts", "edits": [{"old_string": "a", "new_string": "b\nc"}]},
        ),
        ("afterAgentResponse", Kind.AGENT_RESPONSE, {"text": "I updated the mapper."}),
        ("stop", Kind.STOP, {"status": "completed", "loop_count": 0}),
        (
            "subagentStop",
            Kind.SUBAGENT_STOP,
            {"subagent_type": "explore", "task": "find", "summary": "found"},
        ),
        (
            "sessionEnd",
            Kind.SESSION_END,
            {"reason": "user_close", "duration_ms": 10, "final_status": "ok"},
        ),
    ],
)
def test_cursor_event_mapping(
    factory: EventFactory, event: str, kind: Kind, extra: dict[str, object]
) -> None:
    """TC-NRM-004: Every subscribed Cursor hook event maps to a normalized kind with its key fields.

    Given: a payload for each Cursor event we subscribe to (fields from the Cursor hooks docs).
    When: normalized.
    Then: the kind matches and the session key is cursor:<conversation_id>.
    """
    built = factory.from_hook(cursor_payload(event, **extra), "cursor", T0)
    assert built.kind is kind
    assert built.session_key == "cursor:conv-1"
    if kind is Kind.FILE_EDIT:
        assert built.payload["lines_added"] == 2
        assert "new_string" not in str(built.payload)  # edit contents are never stored
    if kind is Kind.PROMPT:
        assert built.payload["attachments"] == ["/a.ts"]


def test_unknown_event_and_missing_session(factory: EventFactory) -> None:
    """TC-NRM-005: Unsupported events and payloads without a session id are rejected.

    Given: a PreCompact event, and a Stop event whose session_id is empty.
    When: normalized.
    Then: UnsupportedPayloadError is raised for both.
    """
    with pytest.raises(UnsupportedPayloadError):
        factory.from_hook(claude_payload("PreCompact"), "claude_code", T0)
    with pytest.raises(UnsupportedPayloadError):
        factory.from_hook(claude_payload("Stop", session=""), "claude_code", T0)


def test_prompt_excerpt_is_capped_and_redacted(factory: EventFactory) -> None:
    """TC-NRM-006: Prompts are stored as capped, redacted excerpts with ticket keys extracted.

    Given: a 2,000-character prompt mentioning EIT-8403 and containing a password.
    When: normalized.
    Then: the excerpt is at most 601 characters, the password is redacted, the full length
          is recorded and EIT-8403 is detected.
    """
    prompt = "Work on EIT-8403. password=hunter22 " + "z" * 2000
    built = factory.from_hook(claude_payload("UserPromptSubmit", prompt=prompt), "claude_code", T0)
    assert len(built.payload["excerpt"]) <= 601
    assert "hunter22" not in built.payload["excerpt"]
    assert built.payload["chars"] == len(prompt)
    assert built.ticket_keys == ["EIT-8403"]
    assert "password_assignment" in built.redaction_rules


def test_shell_output_dropped_for_sensitive_files(factory: EventFactory) -> None:
    """TC-NRM-007: Output of commands that read secret files is never stored.

    Given: a Bash tool call `cat .env` whose stdout contains DB credentials.
    When: normalized.
    Then: the payload has no output, and output_dropped=sensitive_path.
    """
    payload = claude_payload(
        "PostToolUse",
        tool_name="Bash",
        tool_use_id="t-env",
        tool_input={"command": "cat .env"},
        tool_response={"stdout": "DB_PASSWORD=supersecret\nDB_HOST=x", "stderr": ""},
    )
    built = factory.from_hook(payload, "claude_code", T0)
    assert built.payload["output_dropped"] == "sensitive_path"
    assert "supersecret" not in str(built.payload)


def test_long_go_output_counts_before_truncation(factory: EventFactory) -> None:
    """TC-NRM-008: Test counts come from the full output even when the stored tail is short.

    Given: `go test -v` output with 300 passing tests, about 9 KB, well over the 4 KB tail.
    When: normalized.
    Then: test_result has passed=300, and only the tail is stored.
    """
    lines = "".join(f"--- PASS: TestCase{i} (0.00s)\n" for i in range(300))
    output = lines + "ok  \tgithub.com/ei/x\t1.2s\n"
    payload = claude_payload(
        "PostToolUse",
        tool_name="Bash",
        tool_use_id="t-go",
        tool_input={"command": "go test -v ./..."},
        tool_response={"stdout": output, "stderr": ""},
    )
    built = factory.from_hook(payload, "claude_code", T0)
    assert built.payload["test_result"]["passed"] == 300
    assert len(built.payload["output_tail"]) <= 4001


@pytest.mark.parametrize(
    ("cwd", "payload", "expected"),
    [
        ("/w", {"file_path": "/w/repo-a/src/x.go"}, "/w/repo-a/src"),
        ("/w", {"command": "cd /w/repo-b && go test ./..."}, "/w/repo-b"),
        ("/w", {"command": "cd repo-c; npm test"}, "/w/repo-c"),
        ("/w", {"command": 'cd "/w/with space" && ls'}, "/w/with space"),
        ("/w", {"command": "npm test"}, "/w"),
        (None, {}, None),
    ],
)
def test_effective_dir(cwd: str | None, payload: dict[str, object], expected: str | None) -> None:
    """TC-NRM-009: Repo attribution uses the touched file, or a leading `cd`, before the session cwd.

    Given: agents run from a parent folder of many repos.
    When: effective_dir() looks at file paths and `cd X &&` prefixes.
    Then: the most specific directory is returned.
    """
    assert effective_dir(cwd, payload) == expected


def test_repo_attribution_from_file_path(factory: EventFactory, tmp_path: Path) -> None:
    """TC-NRM-010: An edit in a nested repo is attributed to that repo and its branch.

    Given: a session cwd that is a plain folder holding a git repo on branch EIT-8403-billing.
    When: an Edit tool call on a file inside that repo is normalized.
    Then: repo = the repo folder name, branch = EIT-8403-billing, and the ticket comes from the branch.
    """
    repo = tmp_path / "workspace" / "billing-service"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "EIT-8403-billing")
    (repo / "a.txt").write_text("a")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "init")
    payload = claude_payload(
        "PostToolUse",
        cwd=str(tmp_path / "workspace"),
        tool_name="Edit",
        tool_use_id="t-edit",
        tool_input={"file_path": str(repo / "a.txt"), "old_string": "a", "new_string": "b"},
    )
    built = factory.from_hook(payload, "claude_code", T0)
    assert (built.repo, built.branch) == ("billing-service", "EIT-8403-billing")
    assert built.ticket_keys == ["EIT-8403"]
    assert built.payload["is_edit"] is True
    assert "new_string" not in built.payload


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("EIT-8403 and eit-8383, also CA-0214", ["EIT-8403", "EIT-8383", "CA-0214"]),
        ("UTF-8 SHA-256 ISO-8601", []),
        ("test case TC-UN-9701", []),
        ("branch feature/EIT-9286-move", ["EIT-9286"]),
        ("XEIT-123 is not EIT", []),
    ],
)
def test_ticket_matcher(text: str, expected: list[str]) -> None:
    """TC-NRM-011: Ticket keys are found only for configured projects.

    Given: text with real keys, look-alikes (UTF-8, SHA-256) and test-case IDs.
    When: TicketMatcher(["EIT", "CA"]) runs.
    Then: only real ticket keys are returned, upper-cased, in order.
    """
    assert TicketMatcher(["EIT", "CA"]).find(text) == expected


def test_ticket_matcher_empty_allowlist() -> None:
    """TC-NRM-012: An empty project list matches nothing.

    Given: TicketMatcher([]).
    When: text with EIT-1 is scanned.
    Then: no keys are returned.
    """
    assert TicketMatcher([]).find("EIT-1") == []


def test_repo_resolver_outside_repo_and_cache(tmp_path: Path) -> None:
    """TC-NRM-013: Non-repo and missing directories resolve to nothing, and lookups are cached.

    Given: a plain directory, a non-existent path and None.
    When: resolved twice.
    Then: all fields are None, and the second lookup is served from cache.
    """
    resolver = RepoResolver(ttl_s=60)
    plain = tmp_path / "plain"
    plain.mkdir()
    assert resolver.resolve(str(plain)).root is None
    assert resolver.resolve(str(tmp_path / "missing")).name is None
    assert resolver.resolve(None).branch is None
    assert resolver.resolve(str(plain)) is resolver.resolve(str(plain))


def test_secret_on_truncation_boundary_is_fully_removed(factory: EventFactory) -> None:
    """TC-NRM-014: A secret straddling the excerpt cut-off never leaks partially.

    Given: a prompt whose GitHub token starts 10 characters before the 600-character excerpt limit.
    When: normalized.
    Then: no fragment of the token body appears, because redaction runs before truncation,
          and the rule is still reported.
    """
    token_body = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    prompt = "x" * 590 + " ghp_" + token_body + " tail"
    built = factory.from_hook(claude_payload("UserPromptSubmit", prompt=prompt), "claude_code", T0)
    assert token_body[:8] not in built.payload["excerpt"]
    assert "github_token" in built.redaction_rules


def test_configured_repo_wins_without_git(tmp_path: Path) -> None:
    """TC-NRM-015: A path under a configured repo is attributed by config, even when the folder no longer exists.

    Given: config repos /work/billing-service and /work/billing-service/tools/sub (nested), neither on disk.
    When: commands `cd /work/billing-service && npm test` and an edit of /work/billing-service/tools/sub/x.ts are normalized.
    Then: repos are billing-service and sub, the most specific match.
    """
    from workdigest.config import Config
    from workdigest.pipeline import build_factory

    cfg = Config.model_validate(
        {
            "repos": [
                {"path": "/work/billing-service"},
                {"path": "/work/billing-service/tools/sub"},
            ]
        }
    )
    factory = build_factory(cfg)
    shell = factory.from_hook(
        claude_payload(
            "PostToolUse",
            tool_name="Bash",
            tool_use_id="a",
            tool_input={"command": "cd /work/billing-service && npm test"},
        ),
        "claude_code",
        T0,
    )
    edit = factory.from_hook(
        claude_payload(
            "PostToolUse",
            tool_name="Edit",
            tool_use_id="b",
            tool_input={"file_path": "/work/billing-service/tools/sub/x.ts"},
        ),
        "claude_code",
        T0,
    )
    assert (shell.repo, edit.repo) == ("billing-service", "sub")


def test_event_times_are_stored_in_local_time(factory: EventFactory) -> None:
    """TC-NRM-016: Event timestamps are converted to the machine's local timezone.

    Given: a UTC timestamp (as in Claude transcripts).
    When: an event is built.
    Then: the stored ts uses the local offset and is the same instant.
    """
    from datetime import UTC

    from workdigest.clock import local_tz

    utc = datetime(2026, 10, 23, 5, 0, tzinfo=UTC)
    built = factory.from_hook(
        claude_payload("Stop", last_assistant_message="x"), "claude_code", utc
    )
    assert built.ts == utc
    assert built.ts.utcoffset() == datetime.now(tz=local_tz()).utcoffset()


def test_moved_folder_matches_configured_repo_name() -> None:
    """TC-NRM-017: An old path whose folder was moved still maps to the configured repo by name.

    Given: config repo ~/Desktop/consolidated-services/services/billing-service, and a transcript
           command `cd /Users/x/Desktop/microservices-redesign/billing-service && npm test`.
    When: normalized.
    Then: repo = billing-service (matched by folder name), and an unrelated folder gets no repo.
    """
    from workdigest.config import Config
    from workdigest.pipeline import build_factory

    cfg = Config.model_validate(
        {"repos": [{"path": "/nonexistent/consolidated-services/services/billing-service"}]}
    )
    factory = build_factory(cfg)
    moved = factory.from_hook(
        claude_payload(
            "PostToolUse",
            tool_name="Bash",
            tool_use_id="m1",
            tool_input={
                "command": "cd /Users/x/Desktop/microservices-redesign/billing-service && npm test"
            },
        ),
        "claude_code",
        T0,
    )
    other = factory.from_hook(
        claude_payload(
            "PostToolUse",
            tool_name="Bash",
            tool_use_id="m2",
            cwd="/nonexistent/elsewhere",
            tool_input={"command": "ls"},
        ),
        "claude_code",
        T0,
    )
    assert moved.repo == "billing-service"
    assert other.repo is None
