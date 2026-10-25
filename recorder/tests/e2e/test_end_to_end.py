"""End to end: a synthetic day through real hooks, the daemon, the cloud worker and the app contract.

hooks (real script, subprocess) → spool → daemon tick (real ingest) → 20:00 scheduled job
(as pg_cron queues it) → draft + push to an in-memory Supabase → nudge on the Mac and in the
app's notifications → send reminder → "marked sent" in the app silences it.

The rows the app would read are compared with ``app/test/fixtures/e2e_cloud.json``, which the
Flutter suite renders (TC-APP-102, TC-APP-103). Regenerate it with ``make e2e-fixture`` after a sync change.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.cloud.fake_tables import FakeTables
from tests.conftest import claude_payload, cursor_payload, run_hook
from workdigest.clock import now_local, work_date
from workdigest.config import Config
from workdigest.daemon import CloudBridge, Daemon
from workdigest.paths import Paths
from workdigest.store.db import Store

APP_FIXTURE = Path(__file__).resolve().parents[3] / "app" / "test" / "fixtures" / "e2e_cloud.json"
APP_TABLES = (
    "days",
    "digests",
    "digest_statements",
    "evidence",
    "notifications",
    "asks",
    "standing_rules",
)
VITEST = (
    " ✓ src/otp.test.ts (12 tests)\n\n Test Files  3 passed (3)\n      Tests  644 passed (644)\n"
)


class Notes:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def notify(self, title: str, body: str) -> None:
        self.sent.append((title, body))


def _fire_hooks(paths: Paths, cwd: Path) -> None:
    env = {**os.environ, "WORKDIGEST_HOME": str(paths.home)}
    events: list[tuple[dict[str, Any], str]] = [
        (
            claude_payload("SessionStart", session="S-e2e", cwd=str(cwd), source="startup"),
            "claude_code",
        ),
        (
            claude_payload(
                "UserPromptSubmit",
                session="S-e2e",
                cwd=str(cwd),
                prompt="EIT-8403 fix the OTP expiry bug",
            ),
            "claude_code",
        ),
        (
            claude_payload(
                "PostToolUse",
                session="S-e2e",
                cwd=str(cwd),
                tool_name="Bash",
                tool_use_id="t-1",
                tool_input={"command": "npm test"},
                tool_response={"stdout": VITEST, "stderr": ""},
            ),
            "claude_code",
        ),
        (claude_payload("Stop", session="S-e2e", cwd=str(cwd)), "claude_code"),
        (
            cursor_payload(
                "beforeSubmitPrompt",
                conversation="C-e2e",
                root=str(cwd),
                prompt="review the EIT-8403 diff",
            ),
            "cursor",
        ),
    ]
    for payload, source in events:
        result = run_hook(payload, source=source, env=env)
        assert result.returncode == 0, result.stderr


def _app_rows(tables: FakeTables) -> dict[str, list[dict[str, Any]]]:
    return {name: tables.rows.get(name, []) for name in APP_TABLES}


@pytest.fixture
def cloud_config(config: Config) -> Config:
    data = config.model_dump(mode="json")
    data["cloud"] = {
        **data["cloud"],
        "enabled": True,
        "device_id": "mac-e2e",
        "url": "https://e2e.supabase.co",
    }
    data["digest"] = {**data["digest"], "provider": "none", "project": "Platform Consolidation"}
    return Config.model_validate(data)


def test_synthetic_day_end_to_end(paths: Paths, cloud_config: Config, tmp_path: Path) -> None:
    """TC-E2E-001: A day of agent work becomes a pushed draft, a nudge, a reminder and a silenced reminder.

    Given: real hook invocations (Claude session with a vitest run of 644/644, one Cursor prompt),
           and a cloud with the 20:00 scheduled draft job queued (nudge: true).
    When: the daemon ticks at 20:05 and again at 20:46, then the app marks the digest sent.
    Then: the job is done; the pushed digest cites the 644/644 test run and EIT-8403 with evidence;
          "Draft ready – send before 9pm" appears on the Mac and as an app notification; one
          "Send before 21:00" reminder fires; afterwards the cloud reports the digest as sent.
    """
    repo = tmp_path / "billing-service"
    repo.mkdir()
    _fire_hooks(paths, repo)
    assert list(paths.spool_dir.glob("*.jsonl")), "hooks wrote nothing to the spool"

    day = work_date(now_local(), cloud_config.day.boundary_hour)
    tz = now_local().tzinfo
    current = [datetime.combine(day, time(20, 5), tzinfo=tz)]
    tables = FakeTables()
    # What run_schedule() inserts at the nudge time.
    tables.insert(
        "generation_jobs",
        [
            {
                "kind": "full",
                "params": {"work_date": day.isoformat(), "nudge": True},
                "created_at": "1",
            }
        ],
    )
    notes = Notes()
    bridge = CloudBridge(paths, cloud_config, notes, rest_factory=lambda: tables)
    daemon = Daemon(
        paths,
        cloud_config,
        clock=lambda: current[0],
        notifier=notes,
        cloud_tick=bridge.tick,
        digest_sent=bridge.digest_sent,
    )

    ran = daemon.tick()
    assert ran[:4] == ["spool", "collect", "atlassian", "cloud"]
    job = tables.rows["generation_jobs"][0]
    assert job["status"] == "done", job.get("error")
    digest = next(d for d in tables.rows["digests"] if d["work_date"] == day.isoformat())
    assert "644/644" in digest["text"] or "644 passed" in digest["text"]
    assert "EIT-8403" in digest["text"]
    assert any(
        e.get("kind") == "test_run" and "644" in json.dumps(e) for e in tables.rows["evidence"]
    )
    assert tables.rows["notifications"][0]["title"] == "Draft ready – send before 9pm"
    assert notes.sent[0][0] == "Draft ready – send before 9pm"
    assert notes.sent[0][1].endswith("gaps to confirm")

    store = Store.open(paths.db_path)
    try:
        assert (
            store.scalar(
                "SELECT COUNT(DISTINCT session_key) FROM events WHERE work_date = ?",
                (day.isoformat(),),
            )
            == 2
        )
    finally:
        store.close()

    current[0] += timedelta(minutes=41)  # 20:46
    daemon.tick()
    assert [t for t, _ in notes.sent if t.startswith("Send before")] == ["Send before 21:00"]

    tables.update("digests", {"digest_id": f"eq.{digest['digest_id']}"}, {"status": "sent_marked"})
    assert bridge.digest_sent(day.isoformat()) is True

    rows = _app_rows(tables)
    if os.environ.get("WORKDIGEST_WRITE_E2E_FIXTURE"):
        APP_FIXTURE.write_text(
            json.dumps(rows, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
        )
    fixture = json.loads(APP_FIXTURE.read_text(encoding="utf-8"))
    for name in APP_TABLES:
        fresh = {key for row in rows[name] for key in row}
        stored = {key for row in fixture[name] for key in row}
        assert fresh == stored, (
            f"{name}: the app fixture is stale; run `make e2e-fixture` ({fresh ^ stored})"
        )
