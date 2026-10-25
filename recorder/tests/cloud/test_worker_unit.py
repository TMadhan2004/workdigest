"""Worker and sync logic against an in-memory PostgREST stand-in (runs everywhere, including CI)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from tests.cloud.fake_tables import FakeTables
from tests.conftest import IST, claude_payload, spool_line
from workdigest.cloud.sync import pull, push
from workdigest.cloud.worker import tick
from workdigest.config import Config
from workdigest.digest.service import DigestContext
from workdigest.ingest.spool import ingest_spool
from workdigest.llm.fake import FakeProvider
from workdigest.paths import Paths
from workdigest.pipeline import build_factory
from workdigest.store.db import Store

DAY = date(2026, 10, 26)
T0 = datetime(2026, 10, 26, 10, 0, tzinfo=IST)


@pytest.fixture
def ctx(store: Store, paths: Paths, config: Config) -> DigestContext:
    cfg = Config.model_validate(
        {**config.model_dump(mode="json"), "digest": {"provider": "none", "manager_name": "Priya"}}
    )
    lines = [
        spool_line(claude_payload("UserPromptSubmit", session="S1", prompt="EIT-8403 work"), T0)
    ]
    (paths.spool_dir / "2026-10-26.jsonl").write_text("\n".join(lines) + "\n")
    ingest_spool(store, build_factory(cfg), paths.spool_dir, today=DAY)
    return DigestContext(store, cfg, paths)


def test_worker_job_kinds(ctx: DigestContext) -> None:
    """TC-WRK-001: The worker runs full, parse_reply and sync jobs, fails bad ones with a reason, and pushes.

    Given: queued jobs of kind full, parse_reply (with text), parse_reply (no text), section (no LLM) and an unknown kind.
    When: tick() runs once with refresh disabled.
    Then: full, parse_reply and sync are done (full produces a digest id and a notification); the
          others are failed with readable errors; the heartbeat and pushed rows exist.
    """
    tables = FakeTables()
    jobs = tables.insert(
        "generation_jobs",
        [
            {"kind": "full", "params": {"work_date": DAY.isoformat()}, "created_at": "1"},
            {
                "kind": "parse_reply",
                "params": {"text": "Hi Arjun, please share the branch list."},
                "created_at": "2",
            },
            {"kind": "parse_reply", "params": {}, "created_at": "3"},
            {
                "kind": "section",
                "params": {"work_date": DAY.isoformat(), "section": "risks"},
                "created_at": "4",
            },
            {"kind": "sync", "params": {"work_date": DAY.isoformat()}, "created_at": "5"},
        ],
    )
    report = tick(ctx, tables, "mac-1", provider_factory=lambda _: None, refresh=False)
    status = {j["kind"] + str(i): j["status"] for i, j in enumerate(tables.rows["generation_jobs"])}
    assert status == {
        "full0": "done",
        "parse_reply1": "done",
        "parse_reply2": "failed",
        "section3": "failed",
        "sync4": "done",
    }
    errors = [j.get("error") for j in tables.rows["generation_jobs"] if j["status"] == "failed"]
    assert any("needs params.text" in e for e in errors)
    assert any("needs an LLM provider" in e for e in errors)
    assert tables.rows["generation_jobs"][0]["result"]["digest_id"]
    assert tables.rows["notifications"][0]["title"] == "Draft ready"
    assert tables.rows["devices"][0]["device_id"] == "mac-1"
    assert report.pushed.get("digests")
    assert len(jobs) == 5


def test_worker_section_job_with_llm(ctx: DigestContext) -> None:
    """TC-WRK-002: A section job regenerates one section with the configured LLM.

    Given: an existing draft and a fake LLM answering with one learning.
    When: a queued section job for "learnings" is processed.
    Then: the job is done and the stored draft contains the new learning.
    """
    tables = FakeTables()
    tables.insert(
        "generation_jobs",
        [{"kind": "full", "params": {"work_date": DAY.isoformat()}, "created_at": "1"}],
    )
    tick(ctx, tables, "mac-1", provider_factory=lambda _: None, refresh=False)
    tables.insert(
        "generation_jobs",
        [
            {
                "kind": "section",
                "params": {"work_date": DAY.isoformat(), "section": "learnings"},
                "created_at": "2",
            }
        ],
    )
    fake = FakeProvider(
        lambda p: (
            '{"statements": [{"text": "Test against real services.", "evidence_ids": []}]}'
            if "Rewrite ONLY" in p.user
            else '{"headline": "x"}'
        )
    )
    tick(ctx, tables, "mac-1", provider_factory=lambda _: fake, refresh=False)
    assert tables.rows["generation_jobs"][1]["status"] == "done"
    assert "Test against real services." in ctx.store.scalar(
        "SELECT text FROM digests ORDER BY created_at DESC LIMIT 1"
    )


def test_pull_watermark_and_idempotent_push(ctx: DigestContext) -> None:
    """TC-WRK-003: Pull only applies app changes newer than the last pull; push re-sends nothing unchanged.

    Given: a pushed ask edited by the app, pulled once.
    When: pull() runs again and push() runs twice.
    Then: the second pull applies nothing, and the second push uploads no rows.
    """
    from workdigest.digest.service import add_reply

    add_reply(ctx.store, ctx.config, "Please raise the OTP bug.", DAY, None, confirm=True)
    tables = FakeTables()
    push(ctx.store, ctx.config, tables, DAY)
    ask = tables.rows["asks"][0]
    ask.update(
        {
            "status": "waiting",
            "reason": "Vikram owns it",
            "updated_by": "app",
            "updated_at": (datetime.now(tz=IST) + timedelta(seconds=30)).isoformat(),
        }
    )
    first = pull(ctx.store, ctx.config, tables, lambda *_: None)
    ask["updated_at"] = "2000-01-01T00:00:00+00:00"
    second = pull(ctx.store, ctx.config, tables, lambda *_: None)
    assert (first.asks, second.asks) == (1, 0)
    assert ctx.store.scalar("SELECT reason FROM asks") == "Vikram owns it"
    push(ctx.store, ctx.config, tables, DAY)
    assert push(ctx.store, ctx.config, tables, DAY) == {}


def test_app_set_rating_survives_mac_push(ctx: DigestContext) -> None:
    """TC-WRK-004: A rating and team set in the app are pulled into the Mac and never wiped by its push.

    Given: a pushed day, then the app sets rating 7 and team [Rahul].
    When: the Mac pushes (it has no rating), then pulls, then pushes again.
    Then: the cloud keeps rating 7 throughout, and the Mac's day_state gets rating 7 and the team.
    """
    tables = FakeTables()
    push(ctx.store, ctx.config, tables, DAY)
    day = next(r for r in tables.rows["days"] if r["work_date"] == DAY.isoformat())
    assert "rating" not in day  # the Mac never sends an empty rating
    day.update({"rating": 7, "team_members": ["Rahul"], "updated_at": "2099-01-01T00:00:00+00:00"})
    push(ctx.store, ctx.config, tables, DAY)
    assert day["rating"] == 7
    report = pull(ctx.store, ctx.config, tables, lambda *_: None)
    assert report.days == 1
    row = ctx.store.query(
        "SELECT rating, team_members FROM day_state WHERE work_date = ?", (DAY.isoformat(),)
    )[0]
    assert (row["rating"], row["team_members"]) == (7, '["Rahul"]')


def test_app_settings_and_template_reach_the_mac(ctx: DigestContext, paths: Paths) -> None:
    """TC-WRK-005: Provider, transmit flags and the active template chosen in the app take effect on the Mac.

    Given: cloud settings with provider claude_cli and prompt_excerpts off, and an active template
           whose project is "Consolidation v2".
    When: pull() runs with overrides_dir = the WorkDigest home, and config/template are reloaded.
    Then: load_config gives provider claude_cli and prompt_excerpts False; load_template gives the app's
          template; a broken overrides file is ignored.
    """
    from workdigest.config import load_config
    from workdigest.digest.template import default_template, load_template

    tables = FakeTables()
    tables.rows["settings"] = [
        {"provider": "claude_cli", "transmit": {"prompt_excerpts": False, "unknown": True}}
    ]
    body = {**default_template().model_dump(mode="json"), "project": "Consolidation v2"}
    tables.rows["templates"] = [{"version": "v2", "active": True, "body": body}]
    report = pull(ctx.store, ctx.config, tables, lambda *_: None, overrides_dir=paths.home)
    assert (report.settings, report.template) == (True, True)
    paths.config_path.write_text('[identity]\nauthor_emails = ["m@x.y"]\n')
    config = load_config(paths.config_path)
    assert (config.digest.provider, config.transmit.prompt_excerpts) == ("claude_cli", False)
    assert load_template(paths.home / "template.toml").project == "Consolidation v2"
    (paths.home / "cloud_settings.json").write_text("{broken")
    assert load_config(paths.config_path).digest.provider == "cursor_cli"


def test_close_day_job_applies_rating(ctx: DigestContext) -> None:
    """TC-WRK-006: The app's "Close day" job carries rating, team and project into the draft header.

    Given: a queued full job with rating 9, team [Vikram] and project "Consolidation".
    When: tick() processes it.
    Then: the stored draft shows "Day rating: 9", the team and the project.
    """
    tables = FakeTables()
    tables.insert(
        "generation_jobs",
        [
            {
                "kind": "full",
                "params": {
                    "work_date": DAY.isoformat(),
                    "rating": 9,
                    "team_members": ["Vikram"],
                    "project": "Consolidation",
                },
                "created_at": "1",
            }
        ],
    )
    tick(ctx, tables, "mac-1", provider_factory=lambda _: None, refresh=False)
    text = ctx.store.scalar("SELECT text FROM digests ORDER BY created_at DESC LIMIT 1")
    assert "Day rating: 9" in text
    assert "Team members: Vikram" in text
    assert "Project: Consolidation" in text


def test_worker_uploads_template_once(ctx: DigestContext) -> None:
    """TC-WRK-007: The Mac uploads its template as the active one only when the cloud has none.

    Given: an empty templates table.
    When: tick() runs twice.
    Then: exactly one active template exists, with the 11 sections, and the second tick uploads nothing.
    """
    tables = FakeTables()
    first = tick(ctx, tables, "mac-1", provider_factory=lambda _: None, refresh=False)
    tables.rows["templates"][0]["active"] = (
        True  # FakeTables stores the bool; PostgREST filters it as "true"
    )
    second = tick(ctx, tables, "mac-1", provider_factory=lambda _: None, refresh=False)
    assert first.pushed.get("templates") == 1
    assert "templates" not in second.pushed
    assert len(tables.rows["templates"]) == 1
    assert len(tables.rows["templates"][0]["body"]["sections"]) == 11


def test_presence_shows_drafting_during_a_job(ctx: DigestContext) -> None:
    """TC-WRK-008: While a job runs the Mac reports "drafting" with the job id, then "idle".

    Given: a queued full draft job and an LLM factory that records the device row when it is called.
    When: tick() runs.
    Then: during the job the device row says drafting with that job id; afterwards it says idle with no job.
    """
    tables = FakeTables()
    (job,) = tables.insert(
        "generation_jobs",
        [{"kind": "full", "params": {"work_date": DAY.isoformat()}, "created_at": "1"}],
    )
    seen: list[tuple[str, str | None]] = []

    def provider(_ctx: DigestContext) -> None:
        device = tables.rows["devices"][0]
        seen.append((device["status"], device["current_job"]))

    tick(ctx, tables, "mac-1", provider_factory=provider, refresh=False)
    assert ("drafting", job["id"]) in seen
    device = tables.rows["devices"][0]
    assert device["status"] == "idle"
    assert device["current_job"] is None
