"""Phase 4: Supabase auth, push/pull and the job worker.

Integration tests run against the **local** Supabase stack (`supabase start`): real GoTrue,
PostgREST and row-level security, never the production project. They are skipped when the
stack is not running. Unit tests cover token rotation, transmit flags and config merging.
"""

from __future__ import annotations

import io
import json
import subprocess
import urllib.error
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import IST, MemoryKeyring, claude_payload, spool_line
from tests.digest.conftest import history_text
from workdigest.cloud.client import REFRESH_SECRET, CloudAuth, CloudError, Rest
from workdigest.cloud.sync import plan_push, pull, push
from workdigest.cloud.worker import tick
from workdigest.config import Config
from workdigest.digest.repository import DigestRepository
from workdigest.digest.service import DigestContext, add_reply, create_draft, import_history
from workdigest.ingest.spool import ingest_spool
from workdigest.integrations.http import HttpError
from workdigest.paths import Paths
from workdigest.pipeline import build_factory
from workdigest.secrets import SecretStore
from workdigest.store.db import Store

DAY = date(2026, 10, 26)
T0 = datetime(2026, 10, 26, 10, 0, tzinfo=IST)
REPO_ROOT = Path(__file__).resolve().parents[3]


def _local_stack() -> dict[str, str] | None:
    try:
        completed = subprocess.run(
            ["supabase", "status", "-o", "env"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    env = {}
    for line in completed.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"')
    return env if env.get("API_URL") and env.get("ANON_KEY") else None


STACK = _local_stack()
needs_stack = pytest.mark.skipif(
    STACK is None, reason="local Supabase stack not running (`supabase start`)"
)


def _cloud_config(base: Config) -> Config:
    assert STACK is not None
    return Config.model_validate(
        {
            **base.model_dump(mode="json"),
            "cloud": {
                "enabled": True,
                "url": STACK["API_URL"],
                "publishable_key": STACK["ANON_KEY"],
                "device_id": "mac-test",
            },
            "digest": {"provider": "none", "manager_name": "Priya"},
        }
    )


def _new_user(config: Config) -> Rest:
    auth = CloudAuth(config.cloud.url, config.cloud.publishable_key)
    email = f"test-{uuid.uuid4().hex[:10]}@ei.study"  # sign-ups only accept the company domain
    auth.sign_up(email, "Str0ng-pass-123!")
    rest = Rest(auth, config.cloud.publishable_key)
    assert rest.user_id
    return rest


@pytest.fixture
def cloud(store: Store, paths: Paths, config: Config) -> tuple[DigestContext, Rest]:
    cfg = _cloud_config(config)
    factory = build_factory(cfg)
    lines = [
        spool_line(
            claude_payload("UserPromptSubmit", session="S1", prompt="EIT-8403 billing work"), T0
        )
    ]
    (paths.spool_dir / "2026-10-26.jsonl").write_text("\n".join(lines) + "\n")
    ingest_spool(store, factory, paths.spool_dir, today=DAY)
    import_history(store, cfg, history_text())
    ctx = DigestContext(store, cfg, paths)
    create_draft(ctx, DAY, llm=None, now=T0 + timedelta(hours=8))
    return ctx, _new_user(cfg)


# -------------------------------------------------------------- integration
@needs_stack
@pytest.mark.supabase
def test_push_is_complete_and_idempotent(cloud: tuple[DigestContext, Rest]) -> None:
    """TC-CLD-001: A push uploads every planned row once; a second push sends nothing.

    Given: imported history plus one generated draft, and a fresh cloud user.
    When: push() runs twice.
    Then: the first push matches the dry-run plan per table, the cloud holds the 5 digests and
          their asks, the second push uploads nothing, and no row contains raw transcript text.
    """
    ctx, rest = cloud
    plan = plan_push(ctx.store, ctx.config, DAY)
    first = push(ctx.store, ctx.config, rest, DAY)
    assert first == plan.counts()
    assert len(rest.select("digests", {"select": "digest_id"})) == 5
    assert len(rest.select("asks", {"select": "ask_id"})) == len(DigestRepository(ctx.store).asks())
    assert push(ctx.store, ctx.config, rest, DAY) == {}
    evidence = rest.select("evidence", {"select": "text"})
    assert evidence
    assert all("transcript" not in row["text"].lower() for row in evidence)


@needs_stack
@pytest.mark.supabase
def test_row_level_security_isolates_users(cloud: tuple[DigestContext, Rest]) -> None:
    """TC-CLD-002: Another signed-in user sees none of my rows through the real API.

    Given: my pushed data and a second user.
    When: the second user selects digests, asks and evidence.
    Then: all are empty, and the second user's write to my digest changes nothing.
    """
    ctx, rest = cloud
    push(ctx.store, ctx.config, rest, DAY)
    other = _new_user(ctx.config)
    for table in ("digests", "asks", "evidence", "manager_replies"):
        assert other.select(table) == [], table
    digest_id = rest.select("digests", {"select": "digest_id", "limit": "1"})[0]["digest_id"]
    assert other.update("digests", {"digest_id": f"eq.{digest_id}"}, {"text": "hacked"}) == []
    assert (
        rest.select("digests", {"digest_id": f"eq.{digest_id}", "select": "text"})[0]["text"]
        != "hacked"
    )


@needs_stack
@pytest.mark.supabase
def test_pull_applies_app_changes(cloud: tuple[DigestContext, Rest]) -> None:
    """TC-CLD-003: Changes made in the app reach the Mac: ask closed, reply items confirmed, a pasted reply, an edited digest.

    Given: pushed data; in the "app" an ask is marked done with evidence, a new reply is pasted
           (source app), and a digest is edited.
    When: pull() runs, the new reply is parsed locally, its items are confirmed in the app, and pull() runs again.
    Then: the local ask is done with the same evidence, the reply exists locally with the app's id,
          confirming feeds the local asks (a restated ask is merged), and the edited digest is stored as an edited version.
    """
    ctx, rest = cloud
    push(ctx.store, ctx.config, rest, DAY)
    ask = rest.select("asks", {"status": "eq.open", "limit": "1"})[0]
    rest.update(
        "asks",
        {"ask_id": f"eq.{ask['ask_id']}"},
        {"status": "done", "evidence": "https://x/EIT-1", "updated_by": "app"},
    )
    rest.insert(
        "manager_replies",
        [
            {
                "user_id": rest.user_id,
                "reply_id": "app-reply-1",
                "raw_text": "Hi Arjun, please raise a Jira bug for the gateway DELETE fix.",
                "source": "app",
            }
        ],
    )
    digest = rest.select("digests", {"origin": "eq.generated", "limit": "1"})[0]
    rest.insert(
        "digests",
        [
            {
                **{k: digest[k] for k in ("work_date", "status", "structure", "gaps")},
                "user_id": rest.user_id,
                "digest_id": "edited-1",
                "origin": "edited",
                "text": "Greetings Priya, edited",
            }
        ],
    )

    def on_new_reply(reply_id: str, text: str, for_date: str | None) -> None:
        add_reply(ctx.store, ctx.config, text, None, None, confirm=False, reply_id=reply_id)

    report = pull(ctx.store, ctx.config, rest, on_new_reply)
    assert (report.asks, report.new_replies, report.edited_digests) == (1, ["app-reply-1"], 1)
    local = next(a for a in DigestRepository(ctx.store).asks() if a.ask_id == ask["ask_id"])
    assert (local.status, local.evidence) == ("done", "https://x/EIT-1")
    assert (
        ctx.store.scalar("SELECT COUNT(*) FROM manager_replies WHERE reply_id = 'app-reply-1'") == 1
    )
    assert (
        ctx.store.scalar("SELECT text FROM digests WHERE origin = 'edited'")
        == "Greetings Priya, edited"
    )

    push(ctx.store, ctx.config, rest, DAY)  # reply items reach the app
    raised_before = sum(a.times_raised for a in DigestRepository(ctx.store).asks())
    for item in rest.select("reply_items", {"reply_id": "eq.app-reply-1"}):
        rest.update(
            "reply_items",
            {"item_id": f"eq.{item['item_id']}"},
            {"status": "confirmed", "updated_by": "app"},
        )
    second = pull(ctx.store, ctx.config, rest, on_new_reply)
    assert second.replies_confirmed == 1
    # The reply restates the "log each as a Jira Bug" ask, so it is merged (times_raised + 1), not duplicated.
    assert sum(a.times_raised for a in DigestRepository(ctx.store).asks()) > raised_before
    assert (
        rest.select("manager_replies", {"reply_id": "eq.app-reply-1", "select": "processed"})[0][
            "processed"
        ]
        is True
    )


@needs_stack
@pytest.mark.supabase
def test_worker_runs_queued_jobs_once(cloud: tuple[DigestContext, Rest]) -> None:
    """TC-CLD-004: The worker claims a queued draft job, generates, notifies and pushes; nothing runs twice.

    Given: a queued "full" job for 26 Oct, a "section" job (no LLM configured, so it must fail), and a
           job already claimed by another Mac.
    When: tick() runs twice.
    Then: the full job is done with a digest id and gap count, a "Draft ready" notification exists, the
          section job is failed with a reason, the other Mac's job is untouched, the heartbeat row exists,
          and the second tick runs nothing.
    """
    ctx, rest = cloud
    uid = rest.user_id
    full = rest.insert(
        "generation_jobs",
        [{"user_id": uid, "kind": "full", "params": {"work_date": DAY.isoformat()}}],
    )[0]
    section = rest.insert(
        "generation_jobs",
        [
            {
                "user_id": uid,
                "kind": "section",
                "params": {"work_date": DAY.isoformat(), "section": "risks"},
            }
        ],
    )[0]
    taken = rest.insert(
        "generation_jobs",
        [{"user_id": uid, "kind": "sync", "status": "running", "claimed_by": "other-mac"}],
    )[0]

    first = tick(ctx, rest, "mac-test", provider_factory=lambda _: None, refresh=False)
    assert first.jobs_done == [full["id"]]
    assert first.jobs_failed == [section["id"]]
    done = rest.select("generation_jobs", {"id": f"eq.{full['id']}"})[0]
    assert done["status"] == "done"
    assert done["result"]["digest_id"]
    assert done["claimed_by"] == "mac-test"
    failed = rest.select("generation_jobs", {"id": f"eq.{section['id']}"})[0]
    assert "needs an LLM provider" in failed["error"]
    assert (
        rest.select("generation_jobs", {"id": f"eq.{taken['id']}"})[0]["claimed_by"] == "other-mac"
    )
    note = rest.select("notifications", {"kind": "eq.draft_ready"})[0]
    assert note["title"] == "Draft ready"
    assert "gaps to confirm" in note["body"]
    assert rest.select("devices", {"device_id": "eq.mac-test"})
    assert rest.select("digests", {"digest_id": f"eq.{done['result']['digest_id']}"})

    second = tick(ctx, rest, "mac-test", provider_factory=lambda _: None, refresh=False)
    assert (second.jobs_done, second.jobs_failed) == ([], [])


@needs_stack
@pytest.mark.supabase
def test_auth_errors_are_clear() -> None:
    """TC-CLD-005: A wrong password and a missing session fail with clear errors.

    Given: the local stack.
    When: signing in with a wrong password, and asking for a session without signing in.
    Then: sign-in raises HttpError (400), and session() raises CloudError telling you to run `digest cloud login`.
    """
    assert STACK is not None
    auth = CloudAuth(STACK["API_URL"], STACK["ANON_KEY"])
    with pytest.raises(HttpError):
        auth.sign_in("nobody@ei.study", "wrong-password")
    with pytest.raises(CloudError, match="digest cloud login"):
        CloudAuth(STACK["API_URL"], STACK["ANON_KEY"]).session()


# --------------------------------------------------------------------- unit
class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def test_refresh_token_rotation_and_expiry(memory_keyring: MemoryKeyring) -> None:
    """TC-CLD-006: Only the refresh token is stored; it rotates on refresh, and sessions refresh 60 s before expiry.

    Given: a fake auth server that issues a new refresh token on every call, and a controllable clock.
    When: signing in, reading the session before and after expiry, and building a new client later.
    Then: the Keychain holds the latest refresh token only (never the access token), a fresh session is
          reused, an expiring one is refreshed, and a new client restores the session from the Keychain.
    """
    counter = {"n": 0}
    requests: list[Any] = []

    def opener(request: Any, timeout: float) -> _Response:
        counter["n"] += 1
        requests.append(request)
        body = {
            "access_token": f"access-{counter['n']}",
            "refresh_token": f"refresh-{counter['n']}",
            "expires_in": 3600,
            "user": {"id": "u1", "email": "m@x.y"},
        }
        return _Response(json.dumps(body).encode())

    now = {"t": 1_000_000.0}
    auth = CloudAuth("https://p.supabase.co", "pub", opener=opener, clock=lambda: now["t"])
    auth.sign_in("m@x.y", "pw")
    assert memory_keyring.data[("WorkDigest", REFRESH_SECRET)] == "refresh-1"
    assert "access-1" not in json.dumps(list(memory_keyring.data.values()))
    assert auth.session().access_token == "access-1"
    now["t"] += 3600 - 30  # inside the 60-second margin
    assert auth.session().access_token == "access-2"
    assert memory_keyring.data[("WorkDigest", REFRESH_SECRET)] == "refresh-2"
    later = CloudAuth("https://p.supabase.co", "pub", opener=opener, clock=lambda: now["t"])
    assert later.session().access_token == "access-3"
    assert requests[0].get_header("Apikey") == "pub"
    later.sign_out()
    assert ("WorkDigest", REFRESH_SECRET) not in memory_keyring.data
    with pytest.raises(CloudError, match="cloud setup"):
        CloudAuth("", "")


def test_concurrent_refresh_uses_the_token_once(memory_keyring: MemoryKeyring) -> None:
    """TC-CLD-009: Two threads needing a fresh session refresh it once (a reused refresh token would revoke it).

    Given: a stored refresh token, no session in memory, and a slow fake auth server.
    When: eight threads ask for the session at the same time.
    Then: the auth server is called exactly once and every thread gets the same access token.
    """
    import threading
    import time as time_module

    memory_keyring.data[("WorkDigest", REFRESH_SECRET)] = "refresh-0"
    calls: list[int] = []

    def opener(request: Any, timeout: float) -> _Response:
        calls.append(1)
        time_module.sleep(0.05)
        body = {
            "access_token": f"access-{len(calls)}",
            "refresh_token": f"refresh-{len(calls)}",
            "expires_in": 3600,
            "user": {"id": "u1", "email": "m@x.y"},
        }
        return _Response(json.dumps(body).encode())

    auth = CloudAuth("https://p.supabase.co", "pub", opener=opener, clock=lambda: 1_000_000.0)
    tokens: list[str] = []
    threads = [
        threading.Thread(target=lambda: tokens.append(auth.session().access_token))
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(calls) == 1
    assert tokens == ["access-1"] * 8


def test_refresh_across_processes_never_reuses_a_token(
    memory_keyring: MemoryKeyring, tmp_path: Path
) -> None:
    """TC-CLD-010: Two processes sharing one sign-in never present a used refresh token (Supabase would revoke it).

    Given: a fake auth server that rejects a reused refresh token with 400 "already used", the daemon (A)
           holding a session in memory, and a one-off script (B) that refreshes meanwhile.
    When: A's access token expires and A refreshes.
    Then: A uses the newest token from the Keychain (rotated by B), not its stale in-memory one, and the
          sign-in keeps working; a revoked session is reported as "run digest cloud login".
    """
    used: set[str] = set()
    issued = {"n": 0}

    def opener(request: Any, timeout: float) -> _Response:
        token = json.loads(request.data or b"{}").get("refresh_token")
        if token is not None:
            if token in used:
                raise urllib.error.HTTPError(
                    request.full_url,
                    400,
                    "Bad Request",
                    {},
                    io.BytesIO(b'{"error_code":"refresh_token_already_used"}'),
                )
            used.add(token)
        issued["n"] += 1
        body = {
            "access_token": f"access-{issued['n']}",
            "refresh_token": f"refresh-{issued['n']}",
            "expires_in": 3600,
            "user": {"id": "u1", "email": "m@x.y"},
        }
        return _Response(json.dumps(body).encode())

    now = {"t": 1_000_000.0}
    lock = tmp_path / "auth.lock"
    daemon = CloudAuth(
        "https://p.supabase.co", "pub", opener=opener, clock=lambda: now["t"], lock_path=lock
    )
    daemon.sign_in("m@x.y", "pw")
    script = CloudAuth(
        "https://p.supabase.co", "pub", opener=opener, clock=lambda: now["t"], lock_path=lock
    )
    assert script.session().access_token == "access-2"  # rotates refresh-1 -> refresh-2
    now["t"] += 3600
    assert (
        daemon.session().access_token == "access-3"
    )  # used refresh-2 from the Keychain, not refresh-1
    assert memory_keyring.data[("WorkDigest", REFRESH_SECRET)] == "refresh-3"

    used.add("refresh-3")  # revoked server-side
    now["t"] += 3600
    with pytest.raises(CloudError, match="digest cloud login"):
        daemon.session()


def test_transmit_flags_limit_what_is_pushed(store: Store, paths: Paths, config: Config) -> None:
    """TC-CLD-007: Turning off evidence, summaries or replies removes those rows from the push plan.

    Given: imported history and a draft.
    When: plan_push() runs with all flags on, then with sync_evidence, sync_session_summaries and sync_replies off.
    Then: with flags on there are evidence and reply rows; with flags off there are none, and digest
          structures never carry the evidence block.
    """
    import_history(store, config, history_text())
    ctx = DigestContext(store, config, paths)
    create_draft(ctx, DAY, llm=None, now=T0)
    on = plan_push(store, config, DAY)
    assert on.counts().get("evidence")
    assert on.counts().get("reply_items")
    off_config = Config.model_validate(
        {
            **config.model_dump(mode="json"),
            "cloud": {
                "sync_evidence": False,
                "sync_session_summaries": False,
                "sync_replies": False,
            },
        }
    )
    off = plan_push(store, off_config, DAY)
    for table in ("evidence", "session_summaries", "manager_replies", "reply_items"):
        assert not off.counts().get(table), table
    assert all("evidence" not in row["structure"] for _, _, row in off.changes["digests"])


def test_cloud_config_merge_keeps_file(paths: Paths) -> None:
    """TC-CLD-008: `cloud setup` merges keys into [cloud] and leaves the rest of config.toml intact.

    Given: a config.toml with [identity] and an existing [cloud] block.
    When: _set_cloud_config() runs twice with new and changed values.
    Then: [identity] is unchanged, keys are updated in place, and the file still validates.
    """
    from workdigest.cloud_cli import _set_cloud_config
    from workdigest.config import load_config

    paths.config_path.write_text(
        '[identity]\nauthor_emails = ["m@x.y"]\n\n[cloud]\nenabled = false\n'
    )
    _set_cloud_config(paths, {"enabled": True, "url": "https://p.supabase.co"})
    _set_cloud_config(paths, {"device_id": "mac-1", "url": "https://q.supabase.co"})
    config = load_config(paths.config_path)
    assert config.identity.author_emails == ["m@x.y"]
    assert (config.cloud.enabled, config.cloud.url, config.cloud.device_id) == (
        True,
        "https://q.supabase.co",
        "mac-1",
    )
    assert paths.config_path.read_text().count("[cloud]") == 1
    SecretStore()  # keyring is the in-memory fake here
