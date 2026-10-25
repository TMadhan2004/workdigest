"""AI provider health: probing, change detection, and the alerts on the Mac and in the app."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from tests.cloud.fake_tables import FakeTables
from tests.conftest import IST
from workdigest.cloud.sync import push_profile
from workdigest.config import Config
from workdigest.daemon import CloudBridge, fresh_config
from workdigest.llm import health
from workdigest.llm.base import LLMError, LLMUnavailableError, Prompt
from workdigest.paths import Paths
from workdigest.store.db import Store

CLOCK = lambda: datetime(2026, 10, 26, 10, 0, tzinfo=IST)  # noqa: E731


class Notes:
    """Records desktop notifications instead of showing them."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def notify(self, title: str, body: str) -> None:
        self.sent.append((title, body))


class Provider:
    """A provider that answers, or raises the given error."""

    def __init__(self, error: LLMError | None = None, binary: str | None = "/bin/x") -> None:
        self.error = error
        self.binary_path = binary

    def complete(self, _prompt: Prompt) -> str:
        if self.error:
            raise self.error
        return "OK"


def _cap(state: health.State, detail: str = "") -> health.Capability:
    return health.Capability(state, detail, "2026-10-26T10:00:00+05:30")


def _cloud(config: Config, provider: str = "cursor_cli") -> Config:
    data = config.model_dump(mode="json")
    data["cloud"] = {
        **data["cloud"],
        "enabled": True,
        "device_id": "mac-1",
        "url": "https://x.supabase.co",
    }
    data["digest"] = {**data["digest"], "provider": provider}
    data["transmit"] = {**data["transmit"], "llm_enabled": True}
    return Config.model_validate(data)


def test_classify_errors() -> None:
    """TC-AIH-001: Provider errors map to a state and a reason a person can act on.

    Given: not-installed, not-logged-in, usage-limit and other errors.
    When: they are classified.
    Then: missing, logged_out, failing (limit) and failing (the message itself).
    """
    assert health.classify(LLMUnavailableError("cursor-agent not installed")) == (
        "missing",
        "not installed",
    )
    assert health.classify(LLMUnavailableError("cursor-agent not logged in"))[0] == "logged_out"
    assert health.classify(LLMError("claude: 429 usage limit reached")) == (
        "failing",
        "subscription or usage limit reached",
    )
    assert health.classify(LLMError("claude: exited with code 3")) == (
        "failing",
        "exited with code 3",
    )


def test_probe_states() -> None:
    """TC-AIH-002: A probe makes a real call; a missing binary is reported without one.

    Given: a provider that answers, one with no binary, and one whose login expired.
    When: each is probed.
    Then: ok, missing and logged_out.
    """
    assert health.probe("cursor_cli", factory=lambda _n: Provider(), clock=CLOCK).ok
    assert (
        health.probe("cursor_cli", factory=lambda _n: Provider(binary=None), clock=CLOCK).state
        == "missing"
    )
    expired = Provider(LLMUnavailableError("cursor-agent not logged in"))
    assert health.probe("cursor_cli", factory=lambda _n: expired, clock=CLOCK).state == "logged_out"


def test_check_reports_transitions_of_the_chosen_provider(store: Store) -> None:
    """TC-AIH-003: Only the chosen provider's changes are reported, and only when they change.

    Given: Cursor chosen; first check Cursor fails, Claude missing.
    When: checked four times (fail, fail, ok, ok) while Claude stays missing.
    Then: a "stopped" change, nothing, a "works again" change, nothing; results are saved.
    """
    states = iter(["logged_out", "logged_out", "ok", "ok"])

    def prober(name: str) -> health.Capability:
        return _cap("missing", "not installed") if name == "claude_cli" else _cap(next(states))

    reported = []
    for _ in range(4):
        caps, changes = health.check(store, "cursor_cli", prober=prober)
        reported.append([c.now_ok for c in changes])
    assert reported == [[False], [], [True], []]
    assert health.load(store)["claude_cli"].state == "missing"
    assert caps["cursor_cli"].ok
    assert health.as_json(caps)["claude_cli"]["detail"] == "not installed"


def test_first_check_that_works_is_quiet(store: Store) -> None:
    """TC-AIH-004: A first check where the chosen provider works says nothing; "none" never alerts.

    Given: no earlier results.
    When: Cursor is chosen and works; then "none" is chosen and every provider fails.
    Then: no changes either time.
    """
    assert health.check(store, "cursor_cli", prober=lambda _n: _cap("ok"))[1] == []
    assert health.check(store, "none", prober=lambda _n: _cap("failing", "x"))[1] == []


def test_change_messages() -> None:
    """TC-AIH-005: The alert names the provider, the reason and the facts-only fallback.

    Given: Cursor logged out, then working again.
    When: the messages are built.
    Then: "Cursor stopped working" with the reason and "facts-only", then "Cursor works again".
    """
    down = health.Change(
        "cursor_cli", False, _cap("logged_out", "signed out; log in again on the Mac")
    )
    title, body = down.message()
    assert title == "Cursor stopped working"
    assert "log in again" in body
    assert "facts-only" in body
    assert health.Change("cursor_cli", True, _cap("ok")).message()[0] == "Cursor works again"


def test_push_profile_only_on_change(store: Store, config: Config) -> None:
    """TC-AIH-006: The manager's and user's names (and AI status) go to settings only when changed.

    Given: a config with manager Priya.
    When: the profile is pushed twice, then with capabilities.
    Then: sent, skipped, sent; the row carries the names and the capabilities.
    """
    tables = FakeTables()
    assert push_profile(store, config, tables)
    assert not push_profile(store, config, tables)
    assert push_profile(store, config, tables, {"cursor_cli": {"state": "ok"}})
    row = tables.rows["settings"][0]
    assert row["manager_name"] == "Priya"
    assert row["display_name"] == config.identity.name
    assert row["capabilities"] == {"cursor_cli": {"state": "ok"}}


def test_bridge_alerts_on_mac_and_phone(paths: Paths, config: Config, store: Store) -> None:
    """TC-AIH-007: When the chosen AI stops working the Mac and the app are both told, and again when it recovers.

    Given: Cursor chosen with the cloud set up.
    When: checked while Cursor fails, then while it works.
    Then: a Mac notification and an `ai_unavailable` app notification, then `ai_restored`;
          the settings row carries the capabilities.
    """
    tables = FakeTables()
    notes = Notes()
    bridge = CloudBridge(paths, _cloud(config), notes, rest_factory=lambda: tables)
    bridge.check_ai(store, prober=lambda _n: _cap("failing", "subscription or usage limit reached"))
    bridge.check_ai(store, prober=lambda _n: _cap("ok"))
    assert [t for t, _ in notes.sent] == ["Cursor stopped working", "Cursor works again"]
    assert [n["kind"] for n in tables.rows["notifications"]] == ["ai_unavailable", "ai_restored"]
    assert tables.rows["settings"][0]["capabilities"]["claude_cli"]["state"] == "ok"


def test_config_reload_picks_up_the_app_choice(paths: Paths, config: Config) -> None:
    """TC-AIH-008: The daemon re-reads config.toml each pass; with no file the given config stays.

    Given: no config.toml, then one choosing Claude.
    When: the config is refreshed.
    Then: the given config first, then provider claude_cli.
    """
    assert fresh_config(paths, config) is config
    paths.config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.config_path.write_text('[digest]\nprovider = "claude_cli"\nmanager_name = "Priya"\n')
    assert fresh_config(paths, config).digest.provider == "claude_cli"


def test_mac_update_is_announced_once(paths: Paths, config: Config, store: Store) -> None:
    """TC-AIH-009: A newer Mac release is announced once, on the Mac and in the app; offline is quiet.

    Given: this install, and a site that says 9.9.0 (twice), then no network.
    When: the daily check runs three times.
    Then: one Mac notification and one `update_available` app notification naming `digest update`.
    """
    from workdigest.setup.team import Team
    from workdigest.update_check import mac_update_to_announce

    tables = FakeTables()
    notes = Notes()
    team = Team(site_url="https://wd.example")
    import workdigest.setup.team as team_module

    original = team_module.load_team
    team_module.load_team = lambda _config: team  # type: ignore[assignment]
    try:
        bridge = CloudBridge(paths, _cloud(config), notes, rest_factory=lambda: tables)
        assert bridge.check_mac_update(store, fetch=lambda _s: {"version": "9.9.0"}) == "9.9.0"
        assert bridge.check_mac_update(store, fetch=lambda _s: {"version": "9.9.0"}) is None
    finally:
        team_module.load_team = original

    def offline(_site: str) -> dict[str, object]:
        raise OSError("no network")

    assert mac_update_to_announce(store, "https://wd.example", fetch=offline) is None
    assert mac_update_to_announce(store, "", fetch=offline) is None
    assert [t for t, _ in notes.sent] == ["WorkDigest 9.9.0 for your Mac"]
    assert "digest update" in notes.sent[0][1]
    assert [n["kind"] for n in tables.rows["notifications"]] == ["update_available"]


def test_app_times_win_and_time_zone(tmp_path: Path) -> None:
    """TC-AIH-010: Times changed in the app apply on the Mac, and the Mac reports its time zone.

    Given: config.toml with send by 21:00 and the app's settings with 18:00:00 / 16:00:00.
    When: the config is loaded, and /etc/localtime points into zoneinfo or nowhere useful.
    Then: send_deadline 18:00 and nudge_time 16:00; the zone is "Asia/Kolkata", else None.
    """
    from workdigest.cloud.sync import mac_timezone
    from workdigest.config import load_config

    (tmp_path / "config.toml").write_text(
        '[digest]\nsend_deadline = "21:00"\nnudge_time = "20:00"\n'
    )
    (tmp_path / "cloud_settings.json").write_text(
        '{"send_deadline": "18:00:00", "nudge_time": "16:00:00", "provider": null}'
    )
    digest = load_config(tmp_path / "config.toml").digest
    assert (digest.send_deadline, digest.nudge_time) == ("18:00", "16:00")
    zones = tmp_path / "usr/share/zoneinfo/Asia"
    zones.mkdir(parents=True)
    (zones / "Kolkata").write_text("")
    (tmp_path / "localtime").symlink_to(zones / "Kolkata")
    assert mac_timezone(tmp_path / "localtime") == "Asia/Kolkata"
    assert mac_timezone(tmp_path / "missing") is None
