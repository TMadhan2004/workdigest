"""The background daemon: one process (run by launchd) that does all recurring work.

| Task | Every | Notes |
|---|---|---|
| spool ingest | 60 s | hook events into SQLite |
| transcripts + git | 5 min | backfill and commits/branches |
| Jira + Bitbucket | 30 min | PRs (open, approvals) and Jira activity; a no-op without tokens |
| AI health | 60 min | is Cursor/Claude still usable; also right after a draft falls back to facts only |
| Mac update | 24 h | a newer release on the website: told once (Mac, and the app with cloud) |
| heartbeat | 20 s | own thread: "Mac connected" and what it is doing, even during slow tasks |
| cloud worker tick | 30 s | pull app edits, run jobs, push (when cloud is set up) |
| session summaries | 60 min | today's sessions; cached by input hash, so unchanged sessions cost nothing |
| local evening draft | at the nudge time | only when cloud sync is off; with cloud, the 20:00 job comes from Supabase |
| send reminder | 15 min before the deadline | once per day, only if today's digest is not marked sent (app or local) |

One process (instead of separate recorder and worker agents) means one writer to SQLite. Each
task is isolated, so a failing task is logged and the others keep running.
"""

from __future__ import annotations

import logging
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING, Protocol

from workdigest.clock import now_local, work_date
from workdigest.config import Config
from workdigest.paths import Paths
from workdigest.store.db import Store

if TYPE_CHECKING:
    from workdigest.cloud.sync import Tables
    from workdigest.cloud.worker import Presence

log = logging.getLogger("workdigest.daemon")

SPOOL_EVERY = timedelta(seconds=60)
HEARTBEAT_EVERY_S = 20
COLLECT_EVERY = timedelta(minutes=5)
ATLASSIAN_EVERY = timedelta(minutes=30)
AI_HEALTH_EVERY = timedelta(minutes=60)
MAC_UPDATE_EVERY = timedelta(hours=24)
CLOUD_EVERY = timedelta(seconds=30)
SUMMARIZE_EVERY = timedelta(minutes=60)
REMINDER_LEAD = timedelta(minutes=15)
TICK_SLEEP_S = 5


class Notifier(Protocol):
    """Shows a desktop notification."""

    def notify(self, title: str, body: str) -> None:
        """Show ``title`` and ``body``."""


def _applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class MacNotifier:
    """macOS Notification Centre via `osascript` (no extra app or permission prompts beyond the first)."""

    def __init__(self, runner: Callable[[list[str]], object] | None = None) -> None:
        """``runner`` defaults to subprocess.run (injectable for tests)."""
        self._run = runner or (
            lambda argv: subprocess.run(argv, check=False, capture_output=True, timeout=10)
        )

    @staticmethod
    def command(title: str, body: str) -> list[str]:
        """The osascript argv, with both strings escaped for AppleScript."""
        script = (
            f"display notification {_applescript_string(body[:240])} "
            f"with title {_applescript_string('WorkDigest')} subtitle {_applescript_string(title[:120])}"
        )
        return ["osascript", "-e", script]

    def notify(self, title: str, body: str) -> None:
        """Post the notification; failures are logged, never raised."""
        try:
            self._run(self.command(title, body))
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("notification failed: %s", exc)


@dataclass(slots=True)
class Task:
    """A recurring job with its interval and last run time."""

    name: str
    every: timedelta
    run: Callable[[], object]
    enabled: Callable[[], bool] = field(default=lambda: True)
    last: datetime | None = None

    def due(self, now: datetime) -> bool:
        """Whether the task should run at ``now``."""
        return self.enabled() and (self.last is None or now - self.last >= self.every)


def fresh_config(paths: Paths, fallback: Config) -> Config:
    """Re-read config.toml plus the app's overrides (provider, transmit).

    Choices made in the app then apply on the next pass without restarting the daemon.
    """
    from workdigest.config import ConfigError, load_config  # noqa: PLC0415

    if not paths.config_path.is_file():
        return fallback  # nothing to re-read (tests, or a config passed in directly)
    try:
        return load_config(paths.config_path)
    except (ConfigError, OSError) as exc:
        log.warning("config reload failed, keeping the previous one: %s", exc)
        return fallback


def _exit_on_signal(signum: int, _frame: object) -> None:
    raise SystemExit(128 + signum)


def _hhmm(value: str) -> tuple[int, int]:
    hours, minutes = value.split(":")[:2]
    return int(hours), int(minutes)


class Daemon:
    """Runs the recurring tasks. Everything time-related is injectable for tests."""

    def __init__(
        self,
        paths: Paths,
        config: Config,
        *,
        clock: Callable[[], datetime] = now_local,
        notifier: Notifier | None = None,
        cloud_tick: Callable[[Store], object] | None = None,
        ingest: Callable[[Store, tuple[str, ...]], object] | None = None,
        summarize: Callable[[Store, str], object] | None = None,
        atlassian: Callable[[Store, str], object] | None = None,
        ai_health: Callable[[Store], object] | None = None,
        mac_update: Callable[[Store], object] | None = None,
        local_draft: Callable[[Store, str], int] | None = None,
        digest_sent: Callable[[str], bool | None] | None = None,
        on_start: Callable[[], None] | None = None,
        on_stop: Callable[[], None] | None = None,
    ) -> None:
        """Bind the daemon to one WorkDigest home; the callables default to the real implementations."""
        self.paths = paths
        self.config = config
        self.clock = clock
        self.notifier = notifier or MacNotifier()
        self._cloud_tick = cloud_tick
        self._ingest = ingest or self._default_ingest
        self._summarize = summarize or self._default_summarize
        self._atlassian = atlassian or self._default_atlassian
        self._ai_health = ai_health or self._default_ai_health
        self._mac_update = mac_update or self._default_mac_update
        self._local_draft = local_draft or self._default_local_draft
        self._digest_sent = digest_sent or self._local_digest_sent
        self._on_start = on_start
        self._on_stop = on_stop
        self._stopped = False
        cloud_on = config.cloud.enabled and cloud_tick is not None
        self.tasks = [
            Task(
                "spool",
                SPOOL_EVERY,
                lambda: self._with_store(lambda s: self._ingest(s, ("spool",))),
            ),
            Task(
                "collect",
                COLLECT_EVERY,
                lambda: self._with_store(
                    lambda s: self._ingest(s, ("claude_transcripts", "cursor_transcripts", "git"))
                ),
            ),
            # Bitbucket PRs (open, approvals) and Jira activity, so the app's Open PRs stay current
            # without waiting for a draft. Runs before the cloud task, which pushes the results.
            Task(
                "atlassian",
                ATLASSIAN_EVERY,
                lambda: self._with_store(lambda s: self._atlassian(s, self._today())),
            ),
            Task(
                "cloud",
                CLOUD_EVERY,
                lambda: self._with_store(self._cloud_tick_safe),
                enabled=lambda: cloud_on,
            ),
            Task(
                "summarize",
                SUMMARIZE_EVERY,
                lambda: self._with_store(lambda s: self._summarize(s, self._today())),
            ),
            # Is the chosen AI still usable (login, subscription)? Hourly, plus after any failure.
            Task("ai_health", AI_HEALTH_EVERY, lambda: self._with_store(self._ai_health)),
            Task("mac_update", MAC_UPDATE_EVERY, lambda: self._with_store(self._mac_update)),
            Task("evening", timedelta(minutes=1), self._evening, enabled=lambda: not cloud_on),
            Task("reminder", timedelta(minutes=1), self._reminder),
        ]

    # ------------------------------------------------------------------ loop
    def tick(self) -> list[str]:
        """Run every due task once; returns the names that ran (failures included)."""
        now = self.clock()
        ran = []
        for task in self.tasks:
            if not task.due(now):
                continue
            task.last = now
            ran.append(task.name)
            try:
                task.run()
            except Exception:  # one task must never stop the daemon
                log.exception("task %s failed", task.name)
        return ran

    def run_forever(self, sleep: Callable[[float], None] = time.sleep) -> None:
        """Tick until the process is stopped (launchd restarts it if it dies)."""
        log.info("daemon started (home %s, cloud %s)", self.paths.home, self.config.cloud.enabled)
        # launchd stops the agent with SIGTERM and kills it if it lingers. Run on_stop (publish
        # "offline") inside the handler: unwinding can block for minutes when the signal lands
        # in the middle of a task, e.g. waiting for in-flight summary calls.
        signal.signal(signal.SIGTERM, self._handle_term)
        if self._on_start:
            self._on_start()
        try:
            while True:
                self.tick()
                sleep(TICK_SLEEP_S)
        finally:
            self._stop_once()
            log.info("daemon stopped")

    def _stop_once(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self._on_stop:
            self._on_stop()

    def _handle_term(self, signum: int, frame: object) -> None:
        self._stop_once()
        _exit_on_signal(signum, frame)

    # ----------------------------------------------------------------- tasks
    def _today(self) -> str:
        return work_date(self.clock(), self.config.day.boundary_hour).isoformat()

    def _with_store(self, action: Callable[[Store], object]) -> None:
        store = Store.open(self.paths.db_path)
        try:
            action(store)
        finally:
            store.close()

    def _cloud_tick_safe(self, store: Store) -> None:
        assert self._cloud_tick is not None  # noqa: S101 - task is only enabled with a tick
        self._cloud_tick(store)

    def _once(self, key: str) -> bool:
        """True the first time ``key`` is seen (stored in the local cloud_meta table)."""
        store = Store.open(self.paths.db_path)
        try:
            if store.scalar("SELECT 1 FROM cloud_meta WHERE key = ?", (key,)):
                return False
            store.conn.execute(
                "INSERT INTO cloud_meta VALUES (?, ?)", (key, self.clock().isoformat())
            )
            return True
        finally:
            store.close()

    def _at_or_after(self, hhmm: str) -> bool:
        now = self.clock()
        hours, minutes = _hhmm(hhmm)
        return (now.hour, now.minute) >= (
            hours,
            minutes,
        ) and now.hour >= self.config.day.boundary_hour

    def _evening(self) -> None:
        """Without cloud sync: draft at the nudge time and notify, once per day."""
        if not self._at_or_after(self.config.digest.nudge_time):
            return
        day = self._today()
        if not self._once(f"evening:{day}"):
            return
        store = Store.open(self.paths.db_path)
        try:
            gaps = self._local_draft(store, day)
        finally:
            store.close()
        self.notifier.notify(
            "Draft ready", f"{gaps} gaps to confirm – run `digest draft` to review"
        )

    def _reminder(self) -> None:
        """Fifteen minutes before the deadline, remind once if today's digest is not marked sent."""
        hours, minutes = _hhmm(self.config.digest.send_deadline)
        now = self.clock()
        deadline = now.replace(hour=hours, minute=minutes, second=0, microsecond=0)
        if not (deadline - REMINDER_LEAD <= now < deadline):
            return
        day = self._today()
        if self._digest_sent(day) is not False:
            return  # sent, or unknown (offline): do not nag
        if self._once(f"reminder:{day}"):
            self.notifier.notify(
                f"Send before {self.config.digest.send_deadline}",
                "Today's digest is not marked as sent yet",
            )

    # -------------------------------------------------------------- defaults
    def _local_digest_sent(self, day: str) -> bool:
        store = Store.open(self.paths.db_path)
        try:
            return bool(
                store.scalar(
                    "SELECT 1 FROM digests WHERE work_date = ? AND status = 'sent_marked'", (day,)
                )
            )
        finally:
            store.close()

    def _default_ingest(self, store: Store, stages: tuple[str, ...]) -> object:
        from workdigest.pipeline import run_pass  # noqa: PLC0415 - keeps daemon import light

        return run_pass(store, self.config, self.paths, stages)

    def _default_ai_health(self, store: Store) -> object:
        """Without cloud sync: probe and tell the user on the Mac only."""
        from workdigest.llm import health  # noqa: PLC0415

        self.config = fresh_config(self.paths, self.config)
        chosen = self.config.digest.provider if self.config.transmit.llm_enabled else "none"
        caps, changes = health.check(store, chosen)
        for change in changes:
            self.notifier.notify(*change.message())
        return caps

    def _default_mac_update(self, store: Store) -> str | None:
        """Tell the user on the Mac when a newer release is out (once per version)."""
        from workdigest.setup.team import load_team  # noqa: PLC0415
        from workdigest.update_check import announcement, mac_update_to_announce  # noqa: PLC0415

        version = mac_update_to_announce(store, load_team(self.config).site_url)
        if version:
            self.notifier.notify(*announcement(version))
        return version

    def _default_atlassian(self, store: Store, day: str) -> object:
        from datetime import date  # noqa: PLC0415

        from workdigest.digest.service import DigestContext, sync_atlassian  # noqa: PLC0415

        report = sync_atlassian(
            DigestContext(store, self.config, self.paths), date.fromisoformat(day)
        )
        for error in report.errors:
            log.warning("atlassian sync: %s", error)
        return report

    def _default_summarize(self, store: Store, day: str) -> object:
        from workdigest.digest.service import make_provider  # noqa: PLC0415
        from workdigest.digest.summarizer import summarize_day  # noqa: PLC0415
        from workdigest.pipeline import build_factory  # noqa: PLC0415

        llm = make_provider(self.config)
        if llm is None:
            return None
        return summarize_day(
            store,
            day,
            redactor=build_factory(self.config).redactor,
            boundary_hour=self.config.day.boundary_hour,
            llm=llm,
            transmit=self.config.transmit,
        )

    def _default_local_draft(self, store: Store, day: str) -> int:
        from datetime import date  # noqa: PLC0415

        from workdigest.digest.service import DigestContext, create_draft, make_provider  # noqa: PLC0415

        _, draft = create_draft(
            DigestContext(store, self.config, self.paths),
            date.fromisoformat(day),
            llm=make_provider(self.config),
        )
        return sum(1 for g in draft.gaps if g.kind != "input")


class CloudBridge:
    """Connects the daemon to Supabase: the worker tick and the "marked sent" check.

    Signs in lazily and again after a failure, so a Mac that boots offline recovers on its own.
    """

    def __init__(
        self,
        paths: Paths,
        config: Config,
        notifier: Notifier,
        rest_factory: Callable[[], Tables] | None = None,
    ) -> None:
        """``rest_factory`` builds the signed-in PostgREST client (injectable for tests)."""
        self.paths = paths
        self.config = config
        self.notifier = notifier
        self._factory = rest_factory or self._default_rest
        self._rest: Tables | None = None
        self._presence: Presence | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _errors() -> tuple[type[Exception], ...]:
        from workdigest.cloud.client import CloudError  # noqa: PLC0415
        from workdigest.integrations.http import HttpError  # noqa: PLC0415

        return (CloudError, HttpError, OSError)

    def _default_rest(self) -> Tables:
        from workdigest.cloud.client import CloudAuth, Rest  # noqa: PLC0415

        auth = CloudAuth(self.config.cloud.url, self.config.cloud.publishable_key)
        return Rest(auth, self.config.cloud.publishable_key)

    def _tables(self) -> Tables:
        with self._lock:
            if self._rest is None:
                self._rest = self._factory()
            return self._rest

    def _reset(self) -> None:
        with self._lock:
            self._rest = None
            self._presence = None

    def presence(self) -> Presence:
        """The shared live status for this Mac (rebuilt after a sign-in failure)."""
        from workdigest.cloud.worker import Presence  # noqa: PLC0415

        tables = self._tables()
        with self._lock:
            if self._presence is None or self._presence.tables is not tables:
                self._presence = Presence(tables, self.config.cloud.device_id or "")
            return self._presence

    def beat(self) -> bool:
        """One heartbeat; False (and a fresh sign-in next time) when the cloud is unreachable."""
        try:
            self.presence().beat()
        except self._errors() as exc:
            self._reset()
            log.warning("heartbeat failed: %s", exc)
            return False
        return True

    def start(self, interval: float = HEARTBEAT_EVERY_S) -> None:
        """Heartbeat on its own thread, so slow tasks (summaries, drafts) never look like "offline"."""

        def loop() -> None:
            self.beat()
            while not self._stop.wait(interval):
                self.beat()

        self._stop.clear()
        self._thread = threading.Thread(target=loop, name="workdigest-heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the heartbeat and tell the app this Mac is going offline."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        try:
            self.presence().set("offline")
        except self._errors() as exc:
            log.warning("could not publish offline status: %s", exc)

    def tick(self, store: Store) -> None:
        """One worker iteration; nudges from the 20:00 job also appear on the Mac."""
        from workdigest.cloud.worker import tick  # noqa: PLC0415
        from workdigest.digest.service import DigestContext  # noqa: PLC0415

        self.config = fresh_config(self.paths, self.config)
        try:
            report = tick(
                DigestContext(store, self.config, self.paths),
                self._tables(),
                self.config.cloud.device_id or "",
                presence=self.presence(),
            )
        except self._errors() as exc:
            self._reset()  # sign in again next time
            log.warning("cloud tick failed: %s", exc)
            return
        for title, body in report.nudges:
            self.notifier.notify(title, body)
        if report.ai_fell_back:  # the chosen AI just failed a draft: find out why and tell the user
            self.check_ai(store)
        if report.jobs_done or report.jobs_failed:
            log.info("jobs done %d, failed %d", len(report.jobs_done), len(report.jobs_failed))

    def check_ai(self, store: Store, prober: Callable[[str], object] | None = None) -> None:
        """Probe the AI providers, publish what works, and announce changes on the Mac and phone."""
        from workdigest.cloud.sync import post_notification, push_profile  # noqa: PLC0415
        from workdigest.llm import health  # noqa: PLC0415

        self.config = fresh_config(self.paths, self.config)
        chosen = self.config.digest.provider if self.config.transmit.llm_enabled else "none"
        caps, changes = health.check(store, chosen, **({"prober": prober} if prober else {}))  # type: ignore[arg-type]
        for change in changes:
            title, body = change.message()
            self.notifier.notify(title, body)
        try:
            tables = self._tables()
            push_profile(store, self.config, tables, health.as_json(caps))
            for change in changes:
                title, body = change.message()
                post_notification(
                    tables, "ai_restored" if change.now_ok else "ai_unavailable", title, body
                )
        except self._errors() as exc:
            self._reset()
            log.warning("could not publish AI status: %s", exc)

    def check_mac_update(
        self, store: Store, fetch: Callable[[str], dict[str, object]] | None = None
    ) -> str | None:
        """A newer Mac release: notify here and in the app (once per version)."""
        from workdigest.cloud.sync import post_notification  # noqa: PLC0415
        from workdigest.setup.team import load_team  # noqa: PLC0415
        from workdigest.update_check import announcement, mac_update_to_announce  # noqa: PLC0415

        site = load_team(self.config).site_url
        version = mac_update_to_announce(store, site, **({"fetch": fetch} if fetch else {}))  # type: ignore[arg-type]
        if not version:
            return None
        title, body = announcement(version)
        self.notifier.notify(title, body)
        try:
            post_notification(self._tables(), "update_available", title, body)
        except self._errors() as exc:
            self._reset()
            log.warning("could not post the update notice: %s", exc)
        return version

    def digest_sent(self, day: str) -> bool | None:
        """True if today's digest is marked sent in the app; None when the cloud is unreachable."""
        try:
            rows = self._tables().select("digests", {"work_date": f"eq.{day}", "select": "status"})
        except self._errors() as exc:
            self._reset()
            log.warning("digest status check failed: %s", exc)
            return None
        return any(row.get("status") == "sent_marked" for row in rows)


def build_daemon(paths: Paths, config: Config, notifier: Notifier | None = None) -> Daemon:
    """The production daemon: cloud bridge when cloud sync is set up, local-only otherwise."""
    notifier = notifier or MacNotifier()
    if config.cloud.enabled and config.cloud.device_id:
        bridge = CloudBridge(paths, config, notifier)
        return Daemon(
            paths,
            config,
            notifier=notifier,
            cloud_tick=bridge.tick,
            digest_sent=bridge.digest_sent,
            ai_health=bridge.check_ai,
            mac_update=bridge.check_mac_update,
            on_start=bridge.start,
            on_stop=bridge.stop,
        )
    return Daemon(paths, config, notifier=notifier)


def setup_logging(paths: Paths) -> None:
    """Rotating log at <home>/logs/daemon.log (1 MB × 3); launchd also captures stderr."""
    root = logging.getLogger("workdigest")
    root.setLevel(logging.INFO)
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    paths.ensure()
    handler = RotatingFileHandler(
        paths.log_dir / "daemon.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
