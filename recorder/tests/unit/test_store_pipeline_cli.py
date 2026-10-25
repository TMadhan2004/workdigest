"""Store migrations, pipeline stage isolation, the day report and the CLI."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tests.conftest import IST, claude_payload, spool_line
from workdigest import cli
from workdigest.config import Config
from workdigest.hooks import installer
from workdigest.models import Event, Kind, Origin, Source
from workdigest.paths import Paths
from workdigest.pipeline import STAGES, run_pass
from workdigest.store.db import Store

T0 = datetime(2026, 10, 23, 12, 0, tzinfo=IST)
runner = CliRunner()


def test_migrations_apply_once(paths: Paths) -> None:
    """TC-STO-001: Every migration is applied once, in order, and re-opening is a no-op.

    Given: a fresh database file.
    When: it is opened twice.
    Then: each packaged .sql migration is recorded once in name order, the second migrate()
          applies nothing, and the file is 0600.
    """
    from importlib import resources

    expected = sorted(
        e.name
        for e in resources.files("workdigest.store.migrations").iterdir()
        if e.name.endswith(".sql")
    )
    first = Store.open(paths.db_path)
    try:
        assert [
            r["name"] for r in first.query("SELECT name FROM schema_migrations ORDER BY name")
        ] == expected
        assert first.migrate() == []
    finally:
        first.close()
    second = Store.open(paths.db_path)
    try:
        assert second.scalar("SELECT COUNT(*) FROM schema_migrations") == len(expected)
    finally:
        second.close()
    assert paths.db_path.stat().st_mode & 0o777 == 0o600


def test_transaction_rolls_back_on_error(store: Store) -> None:
    """TC-STO-002: A failure inside a transaction leaves no partial writes.

    Given: a transaction that inserts an error row and then raises.
    When: it runs.
    Then: the row is not persisted.
    """

    def failing_write() -> None:
        with store.transaction():
            store.log_error("t", "x")
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        failing_write()
    assert store.scalar("SELECT COUNT(*) FROM ingest_errors") == 0


def test_event_check_constraint_rejects_unknown_source(store: Store) -> None:
    """TC-STO-003: The schema rejects invalid sources (defence against bugs upstream).

    Given: a raw INSERT with source='slack'.
    When: executed.
    Then: sqlite raises an IntegrityError.
    """
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO events (event_id, schema_version, source, origin, kind, ts, work_date, ingested_at) "
            "VALUES ('x', 1, 'slack', 'hook', 'prompt', 't', 'd', 'n')"
        )


def test_session_end_and_title_update_session(store: Store) -> None:
    """TC-STO-004: session_end and title payloads update the sessions row.

    Given: a session_start with a title, then a session_end with reason=logout.
    When: both are inserted.
    Then: the session has that title, ended_at and end_reason.
    """

    def event(kind: Kind, payload: dict[str, Any], key: str) -> Event:
        return Event(
            event_id=key,
            source=Source.CLAUDE_CODE,
            origin=Origin.HOOK,
            kind=kind,
            ts=T0,
            work_date="2026-10-23",
            session_key="claude:Z",
            payload=payload,
        )

    store.insert_event(event(Kind.SESSION_START, {"title": "Fix OTP"}, "e1"))
    store.insert_event(event(Kind.SESSION_END, {"reason": "logout"}, "e2"))
    [row] = store.query("SELECT title, ended_at, end_reason FROM sessions")
    assert (row["title"], row["end_reason"]) == ("Fix OTP", "logout")
    assert row["ended_at"]


def test_pipeline_isolates_failing_stage(
    store: Store, config: Config, paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-PIP-001: A crashing stage is logged, and the other stages still run.

    Given: the git stage is patched to raise, and the spool has one valid event.
    When: run_pass runs all stages.
    Then: the spool event is stored, failures names git, and ingest_errors has a pipeline.git row.
    """
    (paths.spool_dir / "2026-10-23.jsonl").write_text(
        spool_line(claude_payload("Stop", last_assistant_message="x"), T0) + "\n"
    )

    def boom(*_: object, **__: object) -> None:
        raise RuntimeError("git exploded")

    monkeypatch.setattr("workdigest.pipeline.poll_repos", boom)
    report = run_pass(store, config, paths)
    assert set(report.stages) == set(STAGES) - {"git"}
    assert "git exploded" in report.failures["git"]
    assert store.scalar("SELECT COUNT(*) FROM events") == 1
    assert store.scalar("SELECT COUNT(*) FROM ingest_errors WHERE stage = 'pipeline.git'") == 1


@pytest.fixture
def cli_home(paths: Paths, tmp_path: Path) -> Paths:
    sources = (
        f'[sources]\nclaude_projects_dir = "{tmp_path / "none-c"}"\n'
        f'cursor_projects_dir = "{tmp_path / "none-x"}"\n'
    )
    paths.config_path.write_text('[identity]\nauthor_emails = ["arjun.k@ei.study"]\n' + sources)
    return paths


def test_cli_today_json(cli_home: Paths) -> None:
    """TC-CLI-001: `digest today --json` ingests the spool and prints the day's evidence as JSON.

    Given: a spool with a prompt and a vitest run on 2026-10-23.
    When: `digest today --date 2026-10-23 --json` runs.
    Then: it exits 0, and the JSON shows 1 session with 1 prompt and a 644/644 test run.
    """
    lines = [
        spool_line(claude_payload("UserPromptSubmit", prompt="EIT-8403 run tests"), T0),
        spool_line(
            claude_payload(
                "PostToolUse",
                tool_name="Bash",
                tool_use_id="t1",
                tool_input={"command": "npm test"},
                tool_response={"stdout": "      Tests  644 passed (644)\n", "stderr": ""},
            ),
            T0,
        ),
    ]
    (cli_home.spool_dir / "2026-10-23.jsonl").write_text("\n".join(lines) + "\n")
    result = runner.invoke(cli.app, ["today", "--date", "2026-10-23", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["sessions"][0]["prompts"] == 1
    assert data["test_runs"][0]["passed"] == 644
    assert data["tickets"] == ["EIT-8403"]


def test_cli_today_table_and_empty(cli_home: Paths) -> None:
    """TC-CLI-002: The human view renders, and an empty day gives guidance instead of a blank screen.

    Given: no events for 2026-10-20, then events for 2026-10-23.
    When: `digest today` runs for each.
    Then: the empty day says "Nothing captured yet", and the other shows the session table.
    """
    empty = runner.invoke(cli.app, ["today", "--date", "2026-10-20"])
    assert empty.exit_code == 0
    assert "Nothing captured yet" in empty.output
    (cli_home.spool_dir / "2026-10-23.jsonl").write_text(
        spool_line(claude_payload("UserPromptSubmit", prompt="hi"), T0) + "\n"
    )
    full = runner.invoke(cli.app, ["today", "--date", "2026-10-23"])
    assert "Agent sessions" in full.output


def test_cli_today_rejects_bad_date(cli_home: Paths) -> None:
    """TC-CLI-003: An invalid --date is a clear usage error (exit 2).

    Given: --date 23-10-2026.
    When: `digest today` runs.
    Then: the exit code is 2 and the message names the expected format.
    """
    result = runner.invoke(cli.app, ["today", "--date", "23-10-2026", "--no-refresh"])
    assert result.exit_code == 2
    assert "YYYY-MM-DD" in result.output


def test_cli_init_and_doctor(paths: Paths, tmp_path: Path) -> None:
    """TC-CLI-004: `digest init` writes a private config once, and `doctor` reports missing pieces.

    Given: an empty home.
    When: init runs twice, then doctor runs.
    Then: config.toml exists with mode 0600 and is not overwritten, and doctor exits 1
          because no hooks are installed.
    """
    first = runner.invoke(cli.app, ["init", "--name", "Arjun K", "--email", "arjun.k@ei.study"])
    assert first.exit_code == 0
    assert "[[repos]]" in first.output
    assert paths.config_path.stat().st_mode & 0o777 == 0o600
    second = runner.invoke(cli.app, ["init"])
    assert "already exists" in second.output
    hook_files = [
        "--claude-settings",
        str(tmp_path / "c.json"),
        "--cursor-hooks",
        str(tmp_path / "x.json"),
    ]
    doctor = runner.invoke(cli.app, ["doctor", *hook_files])
    assert doctor.exit_code == 1
    assert "Config: ok" in doctor.output


def test_cli_hooks_roundtrip(paths: Paths, tmp_path: Path) -> None:
    """TC-CLI-005: `digest hooks install/status/uninstall` work against explicit settings paths.

    Given: temp Claude and Cursor settings paths.
    When: install, status and uninstall run.
    Then: status reports complete after install, and uninstall restores both files to having no WorkDigest entries.
    """
    claude = tmp_path / "claude-settings.json"
    cursor = tmp_path / "cursor-hooks.json"
    args = ["--claude-settings", str(claude), "--cursor-hooks", str(cursor)]
    assert (
        runner.invoke(
            cli.app, ["hooks", "install", *args, "--python", "/usr/bin/python3"]
        ).exit_code
        == 0
    )
    status = runner.invoke(cli.app, ["hooks", "status", *args])
    assert status.output.count("complete") == 2
    assert runner.invoke(cli.app, ["hooks", "uninstall", *args]).exit_code == 0
    assert not installer.claude_status(claude).installed_events
    assert not installer.cursor_status(cursor).installed_events


def test_cli_hooks_install_refuses_bad_json(paths: Paths, tmp_path: Path) -> None:
    """TC-CLI-006: The CLI surfaces installer refusals with exit code 1 and leaves the file intact.

    Given: a corrupt Claude settings file.
    When: `digest hooks install --no-cursor` runs.
    Then: the exit code is 1, the error is printed and the file is unchanged.
    """
    claude = tmp_path / "settings.json"
    claude.write_text("{oops")
    result = runner.invoke(
        cli.app, ["hooks", "install", "--no-cursor", "--claude-settings", str(claude)]
    )
    assert result.exit_code == 1
    assert "refusing to modify" in " ".join(result.output.split())
    assert claude.read_text() == "{oops"


def test_cli_ingest_stage_selection(cli_home: Paths) -> None:
    """TC-CLI-007: `digest ingest --stage` runs only the named stages and rejects unknown ones.

    Given: an empty spool.
    When: `ingest --stage spool` runs, then `ingest --stage nope`.
    Then: the first prints the spool stats and exits 0; the second exits 2.
    """
    ok = runner.invoke(cli.app, ["ingest", "--stage", "spool"])
    assert ok.exit_code == 0
    assert "spool:" in ok.output
    bad = runner.invoke(cli.app, ["ingest", "--stage", "nope"])
    assert bad.exit_code == 2


def test_cli_run_once(cli_home: Paths) -> None:
    """TC-CLI-008: `digest run --once` performs a single full pass and exits.

    Given: one spooled event.
    When: `digest run --once` runs.
    Then: it exits 0 and the event is in the store.
    """
    (cli_home.spool_dir / "2026-10-23.jsonl").write_text(
        spool_line(claude_payload("Stop", last_assistant_message="z"), T0) + "\n"
    )
    assert runner.invoke(cli.app, ["run", "--once"]).exit_code == 0
    store = Store.open(cli_home.db_path)
    try:
        assert store.scalar("SELECT COUNT(*) FROM events") == 1
    finally:
        store.close()


def test_cli_invalid_config_exit_code(paths: Paths) -> None:
    """TC-CLI-009: A broken config.toml stops commands with exit 2 and a readable error.

    Given: a config.toml with invalid TOML.
    When: `digest today` runs.
    Then: the exit code is 2 and "Config error" is printed.
    """
    paths.config_path.write_text("not = [valid")
    result = runner.invoke(cli.app, ["today"])
    assert result.exit_code == 2
    assert "Config error" in result.output


def test_cli_today_renders_commits_tests_and_branches(cli_home: Paths, store: Store) -> None:
    """TC-CLI-010: The human view lists commits, test runs and new branches.

    Given: a stored commit (unpushed), a failing test run and a branch_created event for 2026-10-23.
    When: `digest today --no-refresh` runs.
    Then: the output shows the commit subject with "NO" for pushed, the failed count and the new branch.
    """
    store.upsert_commit(
        {
            "repo": "billing-service",
            "sha": "a" * 40,
            "author_name": "M",
            "author_email": "arjun.k@ei.study",
            "authored_at": T0.isoformat(),
            "committed_at": T0.isoformat(),
            "work_date": "2026-10-23",
            "subject": "feat: billing",
            "branches": '["feat/x"]',
            "pushed": 0,
            "files_changed": 1,
            "insertions": 3,
            "deletions": 1,
            "ticket_keys": '["EIT-8403"]',
        }
    )
    event = Event(
        event_id="b1",
        source=Source.GIT,
        origin=Origin.POLLER,
        kind=Kind.BRANCH_CREATED,
        ts=T0,
        work_date="2026-10-23",
        repo="billing-service",
        branch="EIT-9333-x",
        ticket_keys=["EIT-9333"],
        payload={"branch": "EIT-9333-x"},
    )
    store.insert_event(event)
    from workdigest.models import TestRun

    store.insert_test_run(
        event, TestRun(runner="go", passed=9, failed=1, total=10, summary_line="go test"), "go test"
    )
    result = runner.invoke(
        cli.app, ["today", "--date", "2026-10-23", "--no-refresh"], env={"COLUMNS": "220"}
    )
    text = " ".join(result.output.split())
    assert "feat: billing" in text
    assert "NO" in text
    assert "1 failed" in text
    assert "New branch: billing-service EIT-9333-x" in text
