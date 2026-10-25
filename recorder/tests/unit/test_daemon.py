"""The background daemon: schedule, isolation, evening draft, send reminder and the cloud bridge."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import pytest

from tests.cloud.fake_tables import FakeTables
from tests.conftest import IST, claude_payload, spool_line
from workdigest.cloud.client import CloudError
from workdigest.config import Config
from workdigest.daemon import CloudBridge, Daemon, MacNotifier, build_daemon, setup_logging
from workdigest.paths import Paths
from workdigest.store.db import Store


class Clock:
    """A clock the test moves by hand."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


class Notes:
    """Records desktop notifications instead of showing them."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def notify(self, title: str, body: str) -> None:
        self.sent.append((title, body))


def _cloud(config: Config) -> Config:
    data = config.model_dump(mode="json")
    data["cloud"] = {
        **data["cloud"],
        "enabled": True,
        "device_id": "mac-1",
        "url": "https://x.supabase.co",
    }
    data["digest"] = {**data["digest"], "provider": "none"}
    return Config.model_validate(data)


def _daemon(
    paths: Paths, config: Config, clock: Clock, **overrides: Any
) -> tuple[Daemon, Notes, list[str]]:
    calls: list[str] = []
    notes = Notes()
    defaults: dict[str, Any] = {
        "ingest": lambda _s, stages: calls.append("ingest:" + ",".join(stages)),
        "summarize": lambda _s, day: calls.append(f"summarize:{day}"),
        "atlassian": lambda _s, day: calls.append(f"atlassian:{day}"),
        "local_draft": lambda _s, day: calls.append(f"draft:{day}") or 3,
        "ai_health": lambda _s: calls.append("ai_health"),
        "mac_update": lambda _s: calls.append("mac_update"),
    }
    daemon = Daemon(paths, config, clock=clock, notifier=notes, **{**defaults, **overrides})
    return daemon, notes, calls


def test_daemon_schedule(paths: Paths, config: Config) -> None:
    """TC-DMN-001: Each task runs on its own interval; cloud work is off without a cloud tick.

    Given: a daemon at 10:00 with no cloud tick.
    When: it ticks at 10:00, 10:00:30, 10:01, 10:05 and 11:00.
    Then: everything due runs at 10:00; nothing at 10:00:30; only the spool at 10:01; spool and
          collect at 10:05; Jira/Bitbucket again at 10:30; spool, collect and summaries at 11:00; the
          cloud task never runs.
    """
    clock = Clock(datetime(2026, 10, 26, 10, 0, tzinfo=IST))
    daemon, _, calls = _daemon(paths, config, clock)
    assert daemon.tick() == [
        "spool",
        "collect",
        "atlassian",
        "summarize",
        "ai_health",
        "mac_update",
        "evening",
        "reminder",
    ]
    clock.advance(seconds=30)
    assert daemon.tick() == []
    clock.advance(seconds=30)
    assert daemon.tick() == ["spool", "evening", "reminder"]
    clock.advance(minutes=4)
    ran = daemon.tick()
    assert {"spool", "collect"} <= set(ran)
    assert "summarize" not in ran
    assert "atlassian" not in ran
    clock.advance(minutes=25)  # 10:30
    assert "atlassian" in daemon.tick()
    clock.advance(minutes=30)  # 11:00
    assert {"spool", "collect", "summarize"} <= set(daemon.tick())
    assert not next(t for t in daemon.tasks if t.name == "cloud").enabled()
    assert calls[:4] == [
        "ingest:spool",
        "ingest:claude_transcripts,cursor_transcripts,git",
        "atlassian:2026-10-26",
        "summarize:2026-10-26",
    ]


def test_daemon_isolates_failures(
    paths: Paths, config: Config, caplog: pytest.LogCaptureFixture
) -> None:
    """TC-DMN-002: A failing task is logged with its traceback and the other tasks still run.

    Given: an ingest that raises.
    When: the daemon ticks.
    Then: the summary task still runs and the log names the failed task.
    """
    clock = Clock(datetime(2026, 10, 26, 10, 0, tzinfo=IST))

    def boom(_store: Store, _stages: tuple[str, ...]) -> None:
        raise RuntimeError("disk full")

    daemon, _, calls = _daemon(paths, config, clock, ingest=boom)
    with caplog.at_level(logging.ERROR, logger="workdigest.daemon"):
        ran = daemon.tick()
    assert "summarize" in ran
    assert "summarize:2026-10-26" in calls
    assert "task spool failed" in caplog.text
    assert "disk full" in caplog.text


def test_local_evening_draft_once(paths: Paths, config: Config) -> None:
    """TC-DMN-003: Without cloud sync the daemon drafts at the nudge time and notifies once per day.

    Given: no cloud, nudge time 20:00, a draft with 3 gaps to confirm.
    When: the daemon ticks at 19:59, 20:01 and 20:30.
    Then: nothing at 19:59; one draft and one "Draft ready … 3 gaps to confirm" at 20:01; no repeat at 20:30.
    """
    clock = Clock(datetime(2026, 10, 26, 19, 59, tzinfo=IST))
    daemon, notes, calls = _daemon(paths, config, clock)
    daemon.tick()
    assert notes.sent == []
    assert not any(c.startswith("draft") for c in calls)
    clock.advance(minutes=2)
    daemon.tick()
    clock.advance(minutes=29)
    daemon.tick()
    assert [c for c in calls if c.startswith("draft")] == ["draft:2026-10-26"]
    assert notes.sent == [("Draft ready", "3 gaps to confirm – run `digest draft` to review")]


def test_send_reminder(paths: Paths, config: Config) -> None:
    """TC-DMN-004: Fifteen minutes before 21:00 an unsent digest triggers one reminder; sent or unknown does not.

    Given: send deadline 21:00 and a checker that says "not sent".
    When: the daemon ticks at 20:40, 20:46, 20:50 and 21:01.
    Then: exactly one "Send before 21:00" reminder (at 20:46). With "sent" or "unknown" there is none.
    """
    clock = Clock(datetime(2026, 10, 26, 20, 40, tzinfo=IST))
    daemon, notes, _ = _daemon(
        paths, config, clock, digest_sent=lambda _day: False, local_draft=lambda _s, _d: 0
    )
    for minutes in (0, 6, 4, 11):
        clock.advance(minutes=minutes)
        daemon.tick()
    assert [t for t, _ in notes.sent if t.startswith("Send")] == ["Send before 21:00"]
    for answer in (True, None):
        clock_b = Clock(datetime(2026, 10, 27, 20, 50, tzinfo=IST))
        quiet, quiet_notes, _ = _daemon(paths, config, clock_b, digest_sent=lambda _d, a=answer: a)
        quiet.tick()
        assert not [t for t, _ in quiet_notes.sent if t.startswith("Send")]


def test_local_sent_check(paths: Paths, config: Config, store: Store) -> None:
    """TC-DMN-005: Without cloud, "marked sent" is read from the local digests table.

    Given: a local digest for 2026-10-26 with status sent_marked, none for 2026-10-27.
    When: the default checker is asked about both days.
    Then: True for the 26th and False for the 27th.
    """
    store.conn.execute(
        "INSERT INTO digests (digest_id, work_date, origin, status, text, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("d1", "2026-10-26", "generated", "sent_marked", "x", "2026-10-26T20:00:00+05:30"),
    )
    daemon = Daemon(paths, config, notifier=Notes())
    assert daemon._digest_sent("2026-10-26") is True
    assert daemon._digest_sent("2026-10-27") is False


def test_mac_notifier_escapes_and_never_raises() -> None:
    """TC-DMN-006: Notification text is escaped for AppleScript, and a missing osascript is not fatal.

    Given: a title and body with quotes and backslashes, then a runner that raises OSError.
    When: the command is built and the notification is sent.
    Then: the script quotes are escaped and no exception escapes notify().
    """
    argv = MacNotifier.command('Draft "ready"', "path C:\\x")
    assert argv[:2] == ["osascript", "-e"]
    assert 'subtitle "Draft \\"ready\\""' in argv[2]
    assert '"path C:\\\\x"' in argv[2]
    seen: list[list[str]] = []
    MacNotifier(runner=seen.append).notify("a", "b")
    assert seen
    assert seen[0][0] == "osascript"

    def missing(_argv: list[str]) -> object:
        raise OSError("osascript not found")

    MacNotifier(runner=missing).notify("a", "b")  # logged, not raised


def test_cloud_bridge_nudge_and_recovery(paths: Paths, config: Config, store: Store) -> None:
    """TC-DMN-007: The 20:00 cloud job's nudge appears on the Mac; a sign-in failure is retried next tick.

    Given: a client factory that fails once, then a fake cloud with a queued scheduled draft job (nudge: true).
    When: the bridge ticks twice.
    Then: the first tick logs and keeps going; the second runs the job and shows
          "Draft ready – send before 9pm" with the gap count on the Mac.
    """
    cfg = _cloud(config)
    tables = FakeTables()
    tables.insert(
        "generation_jobs",
        [{"kind": "full", "params": {"work_date": "2026-10-26", "nudge": True}, "created_at": "1"}],
    )
    attempts: list[int] = []

    def factory() -> FakeTables:
        attempts.append(1)
        if len(attempts) == 1:
            raise CloudError("not signed in")
        return tables

    notes = Notes()
    bridge = CloudBridge(paths, cfg, notes, rest_factory=factory)
    bridge.tick(store)
    assert notes.sent == []
    bridge.tick(store)
    assert tables.rows["generation_jobs"][0]["status"] == "done"
    assert notes.sent
    assert notes.sent[0][0] == "Draft ready – send before 9pm"
    assert notes.sent[0][1].startswith("Mon 26 Oct: ")
    assert notes.sent[0][1].endswith("gaps to confirm")


def test_cloud_bridge_sent_status(paths: Paths, config: Config) -> None:
    """TC-DMN-008: The cloud "marked sent" check returns True, False, or None when offline.

    Given: a cloud digest for the 26th marked sent, a draft for the 27th, then an unreachable cloud.
    When: digest_sent is asked.
    Then: True, False and None respectively.
    """
    tables = FakeTables()
    tables.rows["digests"] = [
        {"work_date": "2026-10-26", "status": "sent_marked"},
        {"work_date": "2026-10-27", "status": "draft"},
    ]
    bridge = CloudBridge(paths, _cloud(config), Notes(), rest_factory=lambda: tables)
    assert bridge.digest_sent("2026-10-26") is True
    assert bridge.digest_sent("2026-10-27") is False

    def offline() -> FakeTables:
        raise OSError("network down")

    assert (
        CloudBridge(paths, _cloud(config), Notes(), rest_factory=offline).digest_sent("2026-10-26")
        is None
    )


def test_build_daemon_modes(paths: Paths, config: Config) -> None:
    """TC-DMN-009: With cloud set up the daemon runs the worker and skips the local evening draft; otherwise the reverse.

    Given: a local-only config and a cloud config with a device id.
    When: build_daemon creates each daemon.
    Then: cloud task enabled only for the cloud config; evening task enabled only for the local one.
    """
    local = {t.name: t.enabled() for t in build_daemon(paths, config, Notes()).tasks}
    cloud = {t.name: t.enabled() for t in build_daemon(paths, _cloud(config), Notes()).tasks}
    assert local["cloud"] is False
    assert local["evening"] is True
    assert cloud["cloud"] is True
    assert cloud["evening"] is False


def test_default_tasks_and_logging(paths: Paths, config: Config) -> None:
    """TC-DMN-010: The real ingest and summary tasks run against the home, and logs go to a rotating file.

    Given: a spool line for today and no LLM (provider none).
    When: the default spool task runs and the summary task runs.
    Then: the event is in SQLite, summaries are skipped without error, and logs/daemon.log exists.
    """
    cfg = Config.model_validate(
        {**config.model_dump(mode="json"), "digest": {"provider": "none", "manager_name": "Priya"}}
    )
    now = datetime.now(tz=IST).replace(hour=12)
    (paths.spool_dir / f"{now:%Y-%m-%d}.jsonl").write_text(
        spool_line(claude_payload("UserPromptSubmit", prompt="hi"), now) + "\n"
    )
    daemon = Daemon(paths, cfg, clock=lambda: now, notifier=Notes())
    daemon.tasks[0].run()
    store = Store.open(paths.db_path)
    try:
        assert store.scalar("SELECT COUNT(*) FROM events") >= 1
        assert daemon._default_summarize(store, now.date().isoformat()) is None
    finally:
        store.close()
    setup_logging(paths)
    setup_logging(paths)  # idempotent: one handler
    logging.getLogger("workdigest.daemon").info("hello")
    assert Path(paths.log_dir / "daemon.log").exists()
    root = logging.getLogger("workdigest")
    handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
    assert len(handlers) == 1
    for handler in handlers:
        root.removeHandler(handler)
        handler.close()


def test_heartbeat_thread_and_offline_on_stop(paths: Paths, config: Config) -> None:
    """TC-DMN-011: The heartbeat runs on its own thread, survives a failed sign-in, and says "offline" on stop.

    Given: a cloud bridge whose first sign-in fails, then a fake cloud, and a 10 ms heartbeat interval.
    When: the heartbeat thread runs for a moment and the bridge is stopped.
    Then: last_seen_at keeps being refreshed after the failure, and the final status is offline.
    """
    import time as time_module

    tables = FakeTables()
    attempts: list[int] = []

    def factory() -> FakeTables:
        attempts.append(1)
        if len(attempts) == 1:
            raise CloudError("not signed in")
        return tables

    bridge = CloudBridge(paths, _cloud(config), Notes(), rest_factory=factory)
    bridge.start(interval=0.01)
    deadline = time_module.monotonic() + 2
    seen: set[str] = set()
    while time_module.monotonic() < deadline and len(seen) < 3:
        if tables.rows.get("devices"):
            seen.add(tables.rows["devices"][0]["last_seen_at"])
        time_module.sleep(0.005)
    bridge.stop()
    assert len(seen) >= 2
    assert tables.rows["devices"][0]["status"] == "offline"


def test_run_forever_lifecycle_hooks(paths: Paths, config: Config) -> None:
    """TC-DMN-012: The daemon starts the heartbeat before the first tick and stops it when the process exits.

    Given: a daemon with start/stop hooks and a sleep that ends the process on the second call.
    When: run_forever runs.
    Then: start ran once before ticking, stop ran once on exit, and SIGTERM publishes the stop at once
          (not after a slow task unwinds) and is turned into a clean exit.
    """
    import signal as signal_module

    from workdigest.daemon import _exit_on_signal

    events: list[str] = []
    clock = Clock(datetime(2026, 10, 26, 10, 0, tzinfo=IST))
    daemon, _, _ = _daemon(
        paths,
        config,
        clock,
        on_start=lambda: events.append("start"),
        on_stop=lambda: events.append("stop"),
    )
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise SystemExit(0)

    previous = signal_module.getsignal(signal_module.SIGTERM)
    try:
        with pytest.raises(SystemExit):
            daemon.run_forever(sleep=sleep)
        assert signal_module.getsignal(signal_module.SIGTERM) == daemon._handle_term
    finally:
        signal_module.signal(signal_module.SIGTERM, previous)
    assert events == ["start", "stop"]

    # SIGTERM in the middle of a task: on_stop runs at once (before unwinding), and only once.
    events.clear()
    second, _, _ = _daemon(paths, config, clock, on_stop=lambda: events.append("stop"))
    with pytest.raises(SystemExit):
        second._handle_term(signal_module.SIGTERM, None)
    assert events == ["stop"]
    second._stop_once()
    assert events == ["stop"]
    with pytest.raises(SystemExit) as exit_info:
        _exit_on_signal(signal_module.SIGTERM, None)
    assert exit_info.value.code == 128 + signal_module.SIGTERM


def test_atlassian_task_and_pr_tickets_pushed(paths: Paths, config: Config, store: Store) -> None:
    """TC-DMN-013: The 30-minute Jira/Bitbucket task is a no-op without tokens, and pushed PRs carry their tickets.

    Given: no Atlassian tokens in the Keychain, and one synced PR about EIT-495.
    When: the default Atlassian task runs, then the PR rows for the cloud are built.
    Then: the task reports no errors and syncs nothing; the PR row includes ticket_keys ["EIT-495"].
    """
    from datetime import date as date_type

    from workdigest.cloud.sync import _build_pull_requests

    daemon = Daemon(paths, config, notifier=Notes())
    report = daemon._default_atlassian(store, "2026-10-26")
    assert report.pull_requests == 0
    store.conn.execute(
        "INSERT INTO pull_requests (repo, pr_id, title, url, state, created_at, updated_at, ticket_keys, synced_at) "
        "VALUES ('quiz-service', 3, 'EIT-495 part 1', 'u', 'OPEN', '2026-05-28', '2026-05-28', '[\"EIT-495\"]', '2026-10-26')"
    )
    rows = list(_build_pull_requests(store, config, date_type(2026, 10, 1)))
    assert rows[0]["ticket_keys"] == ["EIT-495"]
