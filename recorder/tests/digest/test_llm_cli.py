"""LLM adapters (with fake CLI binaries), JSON extraction, sandbox exclusion and the digest CLI."""

from __future__ import annotations

import json
import stat
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from tests.conftest import IST, claude_payload, spool_line
from tests.digest.conftest import FIXTURES, history_text
from workdigest import cli
from workdigest.config import Config
from workdigest.digest.summarizer import session_input
from workdigest.ingest.normalize import EventFactory, UnsupportedPayloadError
from workdigest.llm.base import LLMError, LLMUnavailableError, Prompt, extract_json
from workdigest.llm.cli_providers import SANDBOX_PREFIX, ClaudeCliProvider, CursorCliProvider
from workdigest.llm.fake import FakeProvider
from workdigest.paths import Paths

runner = CliRunner()


def _fake_binary(tmp_path: Path, name: str, script: str) -> Path:
    path = tmp_path / name
    path.write_text("#!/usr/bin/env python3\n" + script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


RECORDER = """
import json, os, sys
with open(os.environ["ARGV_LOG"], "w") as f:
    json.dump({"argv": sys.argv[1:], "cwd": os.getcwd()}, f)
print(json.dumps({"type": "result", "is_error": False, "result": '{"ok": true}'}))
"""


@pytest.mark.parametrize("provider_cls", [CursorCliProvider, ClaudeCliProvider])
def test_cli_providers_are_read_only_and_sandboxed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider_cls: type
) -> None:
    """TC-LLM-001: CLI agents run read-only, in an empty WorkDigest sandbox, and their JSON envelope is unwrapped.

    Given: a fake `cursor-agent` or `claude` that records its argv and cwd.
    When: complete() runs.
    Then: cursor gets `-p --mode ask` (read-only) and never --force/--yolo; claude gets `--tools ""`,
          `--setting-sources project` and `--no-session-persistence`; cwd is a temp dir starting with
          the sandbox prefix; the "result" text is returned.
    """
    log = tmp_path / "argv.json"
    monkeypatch.setenv("ARGV_LOG", str(log))
    binary = _fake_binary(tmp_path, provider_cls.binary, RECORDER)
    provider = provider_cls(binary=str(binary), model="m1")
    assert provider.complete(Prompt(system="SYS", user="USER")) == '{"ok": true}'
    recorded = json.loads(log.read_text())
    argv = recorded["argv"]
    assert Path(recorded["cwd"]).name.startswith(SANDBOX_PREFIX)
    assert "--force" not in argv
    assert "--yolo" not in argv
    assert argv[argv.index("--model") + 1] == "m1"
    if provider_cls is CursorCliProvider:
        assert argv[:5] == ["-p", "--mode", "ask", "--output-format", "json"]
        assert "--mode" in argv
        assert argv[-1].startswith("SYS")
    else:
        assert argv[argv.index("--tools") + 1] == ""
        assert argv[argv.index("--setting-sources") + 1] == "project"
        assert "--no-session-persistence" in argv
        assert argv[argv.index("--system-prompt") + 1] == "SYS"
        assert argv[-1] == "USER"


def test_cli_provider_errors(tmp_path: Path) -> None:
    """TC-LLM-002: Missing binaries, auth errors, crashes, is_error envelopes and timeouts are reported distinctly.

    Given: no binary; a binary that says "Authentication required"; one that exits 3; one returning is_error; one that sleeps.
    When: complete() runs.
    Then: LLMUnavailableError for the first two, LLMError for the rest (with the timeout named).
    """
    prompt = Prompt(system="s", user="u")
    with pytest.raises(LLMUnavailableError, match="not installed"):
        _missing().complete(prompt)
    auth = _fake_binary(
        tmp_path,
        "auth",
        'import sys; print("Error: Authentication required. Please run agent login", file=sys.stderr); sys.exit(1)',
    )
    with pytest.raises(LLMUnavailableError, match="not logged in"):
        CursorCliProvider(binary=str(auth)).complete(prompt)
    crash = _fake_binary(
        tmp_path, "crash", 'import sys; print("boom", file=sys.stderr); sys.exit(3)'
    )
    with pytest.raises(LLMError, match="exit 3"):
        CursorCliProvider(binary=str(crash)).complete(prompt)
    errored = _fake_binary(
        tmp_path,
        "errored",
        'import json; print(json.dumps({"is_error": True, "result": "quota exceeded"}))',
    )
    with pytest.raises(LLMError, match="quota exceeded"):
        ClaudeCliProvider(binary=str(errored)).complete(prompt)
    slow = _fake_binary(tmp_path, "slow", "import time; time.sleep(5)")
    with pytest.raises(LLMError, match="timed out"):
        CursorCliProvider(binary=str(slow), timeout_s=1).complete(prompt)


def _missing() -> CursorCliProvider:
    provider = CursorCliProvider()
    provider.binary_path = None
    return provider


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 2}\n```', {"a": 2}),
        ('Here is the result:\n{"a": 3}\nThanks!', {"a": 3}),
        ("[1, 2]", [1, 2]),
    ],
)
def test_extract_json(raw: str, expected: object) -> None:
    """TC-LLM-003: JSON is extracted from plain, fenced and chatty model answers.

    Given: four answer styles.
    When: extract_json() runs.
    Then: the embedded JSON value is returned.
    """
    assert extract_json(raw) == expected


@pytest.mark.parametrize("raw", ["no json here", "{ broken", ""])
def test_extract_json_failures(raw: str) -> None:
    """TC-LLM-004: Answers without valid JSON raise ValueError.

    Given: prose, broken JSON and an empty string.
    When: extract_json() runs.
    Then: ValueError is raised.
    """
    with pytest.raises(ValueError):  # noqa: PT011 - json errors are ValueError subclasses
        extract_json(raw)


class _Answer(BaseModel):
    n: int


def test_complete_json_retries_then_fails() -> None:
    """TC-LLM-005: complete_json retries once with a correction prompt, then raises.

    Given: a fake that answers garbage then valid JSON; then a fake that answers garbage twice.
    When: complete_json runs.
    Then: the first succeeds on the retry (whose prompt mentions the error); the second raises LLMError.
    """
    fake = FakeProvider(["nope", '{"n": 5}'])
    assert fake.complete_json(Prompt("s", "u"), _Answer).n == 5
    assert "not valid JSON" in fake.prompts[1].user
    with pytest.raises(LLMError, match="invalid JSON"):
        FakeProvider(["x", "y"]).complete_json(Prompt("s", "u"), _Answer)
    with pytest.raises(AssertionError, match="ran out"):
        FakeProvider([]).complete(Prompt("s", "u"))


def test_own_sandbox_activity_is_never_recorded(factory: EventFactory, store, paths: Paths) -> None:  # type: ignore[no-untyped-def]
    """TC-LLM-006: Hook events from WorkDigest's own LLM sandbox are dropped quietly (no feedback loop, no error noise).

    Given: a Cursor hook payload whose workspace is /private/var/.../workdigest-llm-abc.
    When: normalized directly, then ingested from the spool.
    Then: normalizing raises the own-sandbox error; the spool ingest skips it with no event and no ingest_errors row.
    """
    from tests.conftest import cursor_payload
    from workdigest.ingest.normalize import OwnSandboxEventError
    from workdigest.ingest.spool import ingest_spool

    t0 = datetime(2026, 10, 26, 10, tzinfo=IST)
    payload = cursor_payload(
        "afterAgentResponse", root="/private/var/folders/x/T/workdigest-llm-abc123", text="hi"
    )
    with pytest.raises(OwnSandboxEventError, match="own LLM sandbox"):
        factory.from_hook(payload, "cursor", t0)
    assert issubclass(OwnSandboxEventError, UnsupportedPayloadError)
    (paths.spool_dir / "2026-10-26.jsonl").write_text(
        spool_line(payload, t0, source="cursor") + "\n"
    )
    stats = ingest_spool(store, factory, paths.spool_dir, today=t0.date())
    assert stats.skipped == 1
    assert store.scalar("SELECT COUNT(*) FROM events") == 0
    assert store.scalar("SELECT COUNT(*) FROM ingest_errors") == 0


def test_session_input_respects_transmit_flags(store, factory: EventFactory, paths: Paths) -> None:  # type: ignore[no-untyped-def]
    """TC-SUM-001: The summarizer input drops whole categories when their transmit flag is off.

    Given: a session with a prompt, an agent message and an edited file.
    When: session_input() is built with all flags on, then all off.
    Then: with flags on, USER/AGENT/Files lines appear; with flags off, none do.
    """
    from workdigest.config import TransmitSettings
    from workdigest.ingest.spool import ingest_spool

    t0 = datetime(2026, 10, 26, 10, tzinfo=IST)
    lines = [
        spool_line(claude_payload("UserPromptSubmit", session="Q", prompt="Refactor billing"), t0),
        spool_line(
            claude_payload(
                "Stop", session="Q", last_assistant_message="Refactored the billing module."
            ),
            t0,
        ),
        spool_line(
            claude_payload(
                "PostToolUse",
                session="Q",
                tool_name="Edit",
                tool_use_id="e1",
                tool_input={"file_path": "/w/billing.go"},
            ),
            t0,
        ),
    ]
    (paths.spool_dir / "2026-10-26.jsonl").write_text("\n".join(lines) + "\n")
    ingest_spool(store, factory, paths.spool_dir)
    on = session_input(store, "claude:Q", "2026-10-26", redactor=factory.redactor, boundary_hour=4)
    off = session_input(
        store,
        "claude:Q",
        "2026-10-26",
        redactor=factory.redactor,
        boundary_hour=4,
        transmit=TransmitSettings(prompt_excerpts=False, agent_messages=False, file_paths=False),
    )
    assert "USER: Refactor billing" in on
    assert "AGENT: Refactored" in on
    assert "Files edited: /w/billing.go" in on
    for marker in ("USER:", "AGENT", "Files edited"):
        assert marker not in off


def test_transcript_agent_text_is_included_and_redacted(
    store, factory: EventFactory, paths: Paths, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    """TC-SUM-002: The Claude transcript's assistant text for that day is added, redacted.

    Given: a session whose transcript has an assistant message containing a password.
    When: session_input() runs.
    Then: the message is present, with the password redacted.
    """
    from workdigest.ingest.spool import ingest_spool

    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-10-26T05:00:00Z",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "All 650 tests pass now. password=hunter22 was in the fixture.",
                        }
                    ]
                },
            }
        )
        + "\n"
    )
    payload = claude_payload("UserPromptSubmit", session="R", prompt="go")
    payload["transcript_path"] = str(transcript)
    (paths.spool_dir / "2026-10-26.jsonl").write_text(
        spool_line(payload, datetime(2026, 10, 26, 10, tzinfo=IST)) + "\n"
    )
    ingest_spool(store, factory, paths.spool_dir)
    text = session_input(
        store, "claude:R", "2026-10-26", redactor=factory.redactor, boundary_hour=4
    )
    assert "All 650 tests pass now." in text
    assert "hunter22" not in text


# --------------------------------------------------------------------- CLI
@pytest.fixture
def cli_env(paths: Paths, tmp_path: Path) -> Paths:
    paths.config_path.write_text(
        '[identity]\nauthor_emails = ["arjun.k@ei.study"]\n'
        f'[sources]\nclaude_projects_dir = "{tmp_path / "c"}"\ncursor_projects_dir = "{tmp_path / "x"}"\n'
        '[digest]\nprovider = "none"\nmanager_name = "Priya"\n'
    )
    return paths


def test_cli_history_asks_rules_flow(cli_env: Paths, tmp_path: Path) -> None:
    """TC-DCL-001: history import → asks → asks set → rules works end to end from the CLI.

    Given: the golden history file.
    When: `history import`, `asks`, `asks set <id> done --evidence …`, `asks --all` and `rules` run.
    Then: asks are listed, one is closed with evidence, and the 12 seed rules are printed.
    """
    history = tmp_path / "history.txt"
    history.write_text(history_text())
    imported = runner.invoke(cli.app, ["history", "import", str(history)])
    assert imported.exit_code == 0, imported.output
    assert "Imported 4 digests and 4 replies" in imported.output
    listed = runner.invoke(cli.app, ["asks"], env={"COLUMNS": "240"})
    first_id = listed.output.split()[0]
    done = runner.invoke(
        cli.app,
        [
            "asks",
            "set",
            first_id,
            "done",
            "--evidence",
            "https://ei-study.atlassian.net/browse/EIT-1",
        ],
    )
    assert done.exit_code == 0, done.output
    bad = runner.invoke(cli.app, ["asks", "set", first_id, "disagree"])
    assert bad.exit_code == 1
    assert "[done]" in runner.invoke(cli.app, ["asks", "--all"], env={"COLUMNS": "240"}).output
    rules = runner.invoke(cli.app, ["rules"], env={"COLUMNS": "240"})
    assert rules.output.count("R0") + rules.output.count("R1") >= 12


def test_cli_check_golden_digest(cli_env: Paths) -> None:
    """TC-DCL-002: `digest check` reports rule results and exits 1 when any rule fails.

    Given: the 19 Oct digest.
    When: `digest check` runs.
    Then: R01-structure is FAIL and the exit code is 1.
    """
    result = runner.invoke(
        cli.app, ["check", str(FIXTURES / "2026-10-19" / "digest.txt")], env={"COLUMNS": "240"}
    )
    assert result.exit_code == 1
    assert "FAIL   R01-structure" in result.output


def test_cli_reply_close_and_draft(cli_env: Paths, tmp_path: Path) -> None:
    """TC-DCL-003: `reply --yes`, `close --rating` and `draft` (provider none) produce a draft and gap list.

    Given: a spooled session for 26 Oct and a pasted reply.
    When: reply, close and draft run.
    Then: the reply items print with types; the draft prints "Greetings Priya,", "Day rating: 9" and a
          "Gaps to confirm" panel; `section` refuses without an LLM; a bad rating exits 2.
    """
    t0 = datetime(2026, 10, 26, 10, tzinfo=IST)
    (cli_env.spool_dir / "2026-10-26.jsonl").write_text(
        spool_line(claude_payload("UserPromptSubmit", prompt="EIT-1 work"), t0) + "\n"
    )
    reply = tmp_path / "reply.txt"
    reply.write_text("Hi Arjun,\n\nPlease raise the OTP bug.\nGood work on the migrator.\n")
    replied = runner.invoke(cli.app, ["reply", str(reply), "--yes"], env={"COLUMNS": "240"})
    assert replied.exit_code == 0, replied.output
    assert "one_time_action" in replied.output
    assert runner.invoke(cli.app, ["close", "--date", "2026-10-26", "--rating", "5"]).exit_code == 2
    assert (
        runner.invoke(
            cli.app, ["close", "--date", "2026-10-26", "--rating", "9", "--team", "Rahul"]
        ).exit_code
        == 0
    )
    drafted = runner.invoke(cli.app, ["draft", "--date", "2026-10-26"], env={"COLUMNS": "240"})
    assert drafted.exit_code == 0, drafted.output
    assert drafted.output.startswith("Greetings Priya,")
    assert "Day rating: 9" in drafted.output
    assert "Gaps to confirm" in drafted.output
    shown = runner.invoke(
        cli.app, ["draft", "--date", "2026-10-26", "--show-input", "--no-refresh"]
    )
    assert "=== SYSTEM ===" in shown.output
    as_json = runner.invoke(cli.app, ["draft", "--date", "2026-10-26", "--json", "--no-refresh"])
    assert json.loads(as_json.output)["work_date"] == "2026-10-26"
    assert runner.invoke(cli.app, ["section", "risks", "--date", "2026-10-26"]).exit_code == 2


def test_cli_section_with_fake_provider(cli_env: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    """TC-DCL-004: `digest section` regenerates via the configured provider and prints the updated draft.

    Given: a stored draft, and make_provider patched to return a fake LLM.
    When: `digest section learnings` runs.
    Then: the new learning appears in the output.
    """
    assert runner.invoke(cli.app, ["draft", "--date", "2026-10-26", "--no-refresh"]).exit_code == 0
    fake = FakeProvider(
        ['{"statements": [{"text": "Check data before planning migrations.", "evidence_ids": []}]}']
    )
    monkeypatch.setattr("workdigest.digest_cli.make_provider", lambda *_: fake)
    result = runner.invoke(
        cli.app, ["section", "learnings", "--date", "2026-10-26"], env={"COLUMNS": "240"}
    )
    assert result.exit_code == 0, result.output
    assert "Check data before planning migrations." in result.output


def test_cli_invalid_date(cli_env: Paths) -> None:
    """TC-DCL-005: Invalid dates are usage errors.

    Given: --date 26/10/2026.
    When: `digest draft` runs.
    Then: the exit code is 2.
    """
    assert runner.invoke(cli.app, ["draft", "--date", "26/10/2026", "--no-refresh"]).exit_code == 2


def test_default_config_has_digest_and_transmit(tmp_path: Path) -> None:
    """TC-DCL-006: The starter config contains the digest provider and transmit flags, and validates.

    Given: render_default_config().
    When: loaded.
    Then: the provider is cursor_cli and llm_enabled is true; an unknown provider is rejected.
    """
    from workdigest.config import ConfigError, load_config, render_default_config

    path = tmp_path / "c.toml"
    path.write_text(render_default_config("M", ["m@x.y"]))
    config = load_config(path)
    assert (config.digest.provider, config.transmit.llm_enabled) == ("cursor_cli", True)
    path.write_text('[digest]\nprovider = "gpt"\n')
    with pytest.raises(ConfigError):
        load_config(path)
    assert Config().digest.send_deadline == "21:00"
