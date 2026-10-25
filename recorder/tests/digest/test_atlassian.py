"""Phase 3: HTTP client, Jira and Bitbucket clients, sync, hygiene and digest integration.

The Jira and Bitbucket clients are the real code; only HTTP is faked, serving response shapes
from the Atlassian REST docs (Jira v3 search/jql, comments, changelog; Bitbucket 2.0
pullrequests and activity). These are contract tests: when a live token is available,
`digest atlassian check` confirms the real shapes.
"""

from __future__ import annotations

import io
import json
import urllib.error
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from typer.testing import CliRunner

from tests.conftest import IST, ME, MemoryKeyring, claude_payload, spool_line
from workdigest import cli
from workdigest.config import Config
from workdigest.digest.draft import render
from workdigest.digest.service import DigestContext, create_draft, hygiene_gaps, sync_atlassian
from workdigest.ingest.spool import ingest_spool
from workdigest.integrations.bitbucket import (
    BitbucketClient,
    human_duration,
    repo_slug,
    review_timeline,
)
from workdigest.integrations.http import AuthError, BasicAuthClient, HttpError
from workdigest.integrations.jira import JiraClient, adf_text
from workdigest.integrations.sync import Clients, make_clients, ticket_hygiene
from workdigest.paths import Paths
from workdigest.pipeline import build_factory
from workdigest.secrets import SecretStore
from workdigest.store.db import Store

DAY = date(2026, 10, 26)
T0 = datetime(2026, 10, 26, 10, 0, tzinfo=IST)
NOW = datetime(2026, 10, 26, 18, 0, tzinfo=IST)
CLOUD = "00000000-0000-4000-8000-000000000001"
JIRA = f"https://api.atlassian.com/ex/jira/{CLOUD}/rest/api/3"
BB = "https://api.bitbucket.org/2.0"
MY_JIRA_ID = "acc-me"
MY_BB_UUID = "{me-uuid}"


class FakeHttp:
    """Routes requests by URL path to canned JSON (or callables); records every call."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, Any]] = []

    def _answer(self, method: str, url: str, payload: Any) -> Any:
        self.calls.append((method, url, payload))
        path = url.split("?", 1)[0]
        if path not in self.routes:
            raise HttpError(404, path, "not found")
        answer = self.routes[path]
        return answer(payload) if callable(answer) else answer

    def get(self, url: str, params: Any = None) -> Any:
        return self._answer("GET", url, params)

    def post(self, url: str, body: dict[str, Any]) -> Any:
        return self._answer("POST", url, body)


def _issue(
    key: str,
    summary: str,
    status: str,
    category: str,
    *,
    assignee: str = MY_JIRA_ID,
    due: str | None = None,
    parent: str | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "status": {"name": status, "statusCategory": {"name": category}},
            "issuetype": {"name": "Task"},
            "duedate": due,
            "parent": {"key": parent} if parent else None,
            "assignee": {"accountId": assignee},
            "updated": "2026-10-26T12:00:00.000+0530",
        },
    }


def _jira_routes() -> dict[str, Any]:
    def search(body: dict[str, Any]) -> dict[str, Any]:
        if "updatedBy" in body["jql"]:
            if body.get("nextPageToken") == "p2":
                return {
                    "issues": [_issue("EIT-8383", "OpenAPI spec writer skill", "To Do", "To Do")],
                    "nextPageToken": "p2",
                }
            return {
                "issues": [
                    _issue(
                        "EIT-8403",
                        "Move billing profiles",
                        "In Progress",
                        "In Progress",
                        due="2026-10-23",
                        parent="EIT-8000",
                    )
                ],
                "nextPageToken": "p2",
            }
        return {
            "issues": [
                _issue("EIT-8403", "Move billing profiles", "In Progress", "In Progress"),
                _issue("EIT-1115", "Old June ticket", "In Progress", "In Progress"),
            ],
            "isLast": True,
        }

    comment_body = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": "Moved credits; password=hunter22 removed from fixture",
                    }
                ],
            }
        ],
    }
    return {
        f"{JIRA}/myself": {"accountId": MY_JIRA_ID, "displayName": "Arjun K"},
        f"{JIRA}/search/jql": search,
        f"{JIRA}/issue/EIT-8403/comment": {
            "comments": [
                {
                    "id": "c1",
                    "created": "2026-10-26T11:00:00.000+0530",
                    "author": {"accountId": MY_JIRA_ID},
                    "body": comment_body,
                },
                {
                    "id": "c0",
                    "created": "2026-10-20T11:00:00.000+0530",
                    "author": {"accountId": MY_JIRA_ID},
                    "body": comment_body,
                },
                {
                    "id": "c2",
                    "created": "2026-10-26T12:00:00.000+0530",
                    "author": {"accountId": "someone"},
                    "body": comment_body,
                },
            ]
        },
        f"{JIRA}/issue/EIT-8403/changelog": {
            "values": [
                {
                    "id": "h1",
                    "created": "2026-10-26T10:30:00.000+0530",
                    "author": {"accountId": MY_JIRA_ID},
                    "items": [
                        {"field": "status", "fromString": "To Do", "toString": "In Progress"},
                        {"field": "description", "fromString": "a", "toString": "b"},
                    ],
                },
            ],
            "isLast": True,
        },
        f"{JIRA}/issue/EIT-8383/comment": {"comments": []},
        f"{JIRA}/issue/EIT-8383/changelog": {"values": [], "isLast": True},
        f"{JIRA}/issue/EIT-1115/comment": {"comments": []},
        f"{JIRA}/issue/EIT-1115/changelog": {"values": [], "isLast": True},
    }


def _bb_routes() -> dict[str, Any]:
    pr_open = {
        "id": 312,
        "title": "EIT-6685 token refresh",
        "state": "OPEN",
        "created_on": "2026-10-05T10:00:00+05:30",
        "updated_on": "2026-10-24T09:00:00+05:30",
        "source": {"branch": {"name": "EIT-6685-refresh"}},
        "destination": {"branch": {"name": "master"}},
        "links": {
            "html": {
                "href": "https://bitbucket.org/ei-india-admin/billing-service/pull-requests/312"
            }
        },
    }
    pr_merged = {
        "id": 41,
        "title": "EIT-8383 skill update",
        "state": "MERGED",
        "created_on": "2026-10-22T18:03:00+05:30",
        "updated_on": "2026-10-23T12:00:00+05:30",
        "source": {"branch": {"name": "EIT-8383"}},
        "destination": {"branch": {"name": "master"}},
        "links": {
            "html": {
                "href": "https://bitbucket.org/ei-india-admin/billing-service/pull-requests/41"
            }
        },
    }
    return {
        f"{BB}/user": {"uuid": MY_BB_UUID, "display_name": "Arjun K"},
        f"{BB}/repositories/ei-india-admin/billing-service/pullrequests": {
            "values": [pr_open],
            "next": f"{BB}/repositories/ei-india-admin/billing-service/pullrequests?page=2",
        },
        f"{BB}/repositories/ei-india-admin/billing-service/pullrequests?page=2": {
            "values": [pr_merged]
        },
        f"{BB}/repositories/ei-india-admin/billing-service/pullrequests/312/activity": {
            "values": [{"comment": {"id": 1}}]
        },
        f"{BB}/repositories/ei-india-admin/billing-service/pullrequests/41/activity": {
            "values": [
                {
                    "approval": {
                        "date": "2026-10-23T08:00:00+05:30",
                        "user": {"uuid": MY_BB_UUID, "display_name": "Arjun K"},
                    }
                },
                {
                    "approval": {
                        "date": "2026-10-23T10:15:00+05:30",
                        "user": {"uuid": "{b}", "display_name": "Rahul"},
                    }
                },
                {"update": {"state": "MERGED", "date": "2026-10-23T12:00:00+05:30"}},
            ]
        },
    }


class PagedFakeHttp(FakeHttp):
    """FakeHttp whose route keys may include a query string (for Bitbucket `next` links)."""

    def _answer(self, method: str, url: str, payload: Any) -> Any:
        self.calls.append((method, url, payload))
        key = url if url in self.routes else url.split("?", 1)[0]
        if key not in self.routes:
            raise HttpError(404, key, "not found")
        answer = self.routes[key]
        return answer(payload) if callable(answer) else answer


@pytest.fixture
def clients() -> Clients:
    jira_http = FakeHttp(_jira_routes())
    bb_http = PagedFakeHttp(_bb_routes())
    return Clients(
        jira=JiraClient(jira_http, CLOUD, "https://ei-study.atlassian.net"),
        bitbucket=BitbucketClient(bb_http, "ei-india-admin"),
    )


@pytest.fixture
def ctx(store: Store, paths: Paths, config: Config) -> DigestContext:
    factory = build_factory(config)
    lines = [
        spool_line(
            claude_payload(
                "UserPromptSubmit", session="S1", prompt="Finish EIT-8403 credits and check CA-0214"
            ),
            T0,
        ),
        spool_line(
            claude_payload("UserPromptSubmit", session="S1", prompt="EIT-8403 tests now"),
            T0 + timedelta(minutes=5),
        ),
    ]
    (paths.spool_dir / "2026-10-26.jsonl").write_text("\n".join(lines) + "\n")
    ingest_spool(store, factory, paths.spool_dir, today=DAY)
    store.upsert_repo(
        "billing-service",
        "/w/billing-service",
        "git@bitbucket.org:ei-india-admin/billing-service.git",
        "https://bitbucket.org/ei-india-admin/billing-service",
    )
    store.upsert_repo("local-only", "/w/local", None, None)
    store.upsert_commit(
        {
            "repo": "billing-service",
            "sha": "a" * 40,
            "author_name": "M",
            "author_email": ME,
            "authored_at": T0.isoformat(),
            "committed_at": T0.isoformat(),
            "work_date": DAY.isoformat(),
            "subject": "feat: EIT-8403 credits",
            "branches": '["feat/billing-migrator"]',
            "pushed": 1,
            "files_changed": 1,
            "insertions": 2,
            "deletions": 0,
            "ticket_keys": '["EIT-8403"]',
        }
    )
    return DigestContext(store, config, paths)


# ------------------------------------------------------------------- HTTP
class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _http_error(code: int, headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://x", code, "err", headers or {}, io.BytesIO(b'{"errorMessages":["nope"]}')
    )  # type: ignore[arg-type]


def test_http_client_auth_retries_and_errors() -> None:
    """TC-ATL-001: Basic auth is sent, 429/5xx are retried with Retry-After, and 401 raises AuthError without the token.

    Given: a fake opener returning 429 (Retry-After 7), then 503, then 200; and separately a 401.
    When: get() runs.
    Then: the third attempt succeeds after sleeping 7 s then 4 s; the Authorization header is Basic
          base64(email:token); the 401 raises AuthError whose message and repr never contain the token;
          the query string is dropped from error messages; plain http is refused.
    """
    attempts: list[Any] = []
    sleeps: list[float] = []

    def opener(request: Any, timeout: float) -> _Response:
        attempts.append(request)
        if len(attempts) == 1:
            raise _http_error(429, {"Retry-After": "7"})
        if len(attempts) == 2:
            raise _http_error(503)
        return _Response(b'{"ok": true}')

    client = BasicAuthClient("me@x.y", "SECRETTOKEN123", sleep=sleeps.append, opener=opener)
    assert client.get("https://api.example/x", {"q": "a b"}) == {"ok": True}
    assert sleeps == [7.0, 4.0]
    assert attempts[0].get_header("Authorization") == "Basic bWVAeC55OlNFQ1JFVFRPS0VOMTIz"
    assert "q=a+b" in attempts[0].full_url

    def denied(request: Any, timeout: float) -> _Response:
        raise _http_error(401)

    bad = BasicAuthClient("me@x.y", "SECRETTOKEN123", opener=denied)
    with pytest.raises(AuthError) as caught:
        bad.get("https://api.example/x", {"jql": "private stuff"})
    assert "SECRETTOKEN123" not in str(caught.value)
    assert "private stuff" not in str(caught.value)
    assert "SECRETTOKEN123" not in repr(bad)
    with pytest.raises(ValueError, match="https"):
        bad.get("http://insecure.example/x")


def test_http_client_gives_up_after_retries() -> None:
    """TC-ATL-002: Persistent 5xx and network errors stop after the retry budget with HttpError.

    Given: an opener that always returns 502, and one that always fails to connect.
    When: get() runs with retries=2.
    Then: HttpError 502 after 3 attempts, and HttpError 0 for the network failure.
    """
    calls: list[int] = []

    def always_502(request: Any, timeout: float) -> _Response:
        calls.append(1)
        raise _http_error(502)

    with pytest.raises(HttpError) as caught:
        BasicAuthClient("a@b.c", "t", retries=2, sleep=lambda _: None, opener=always_502).get(
            "https://x.y/z"
        )
    assert (caught.value.status, len(calls)) == (502, 3)

    def offline(request: Any, timeout: float) -> _Response:
        raise urllib.error.URLError("no route")

    with pytest.raises(HttpError) as offline_error:
        BasicAuthClient("a@b.c", "t", retries=1, sleep=lambda _: None, opener=offline).post(
            "https://x.y/z", {}
        )
    assert offline_error.value.status == 0


# ------------------------------------------------------------------- Jira
def test_jira_search_paging_stops_on_repeated_token(clients: Clients) -> None:
    """TC-ATL-003: JQL search uses POST /search/jql with nextPageToken and stops when a token repeats.

    Given: a search whose second page returns the same nextPageToken again (reported Jira behaviour).
    When: search() is consumed.
    Then: exactly two pages are fetched (no endless loop), via the API gateway URL for scoped tokens.
    """
    assert clients.jira is not None
    issues = list(clients.jira.search('issuekey in updatedBy(currentUser(), "a", "b")'))
    assert [i["key"] for i in issues] == ["EIT-8403", "EIT-8383"]
    http = clients.jira.http
    assert isinstance(http, FakeHttp)
    posts = [c for c in http.calls if c[0] == "POST"]
    assert len(posts) == 2
    assert posts[0][1] == f"{JIRA}/search/jql"
    assert posts[1][2]["nextPageToken"] == "p2"


def test_jira_issue_missing_and_adf() -> None:
    """TC-ATL-004: A missing issue returns None, other errors propagate, and ADF comments become plain text.

    Given: an issue route that 404s, one that 500s, and a nested ADF document.
    When: issue() and adf_text() run.
    Then: None for 404, HttpError for 500, and "Hello world. Next" for the ADF.
    """

    class Failing(FakeHttp):
        def get(self, url: str, params: Any = None) -> Any:
            raise HttpError(500, url, "boom")

    assert JiraClient(FakeHttp({}), CLOUD, "https://s").issue("CA-0214") is None
    with pytest.raises(HttpError):
        JiraClient(Failing({}), CLOUD, "https://s").issue("EIT-1")
    doc = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "Hello"}, {"type": "text", "text": " world."}],
            },
            {"type": "paragraph", "content": [{"type": "text", "text": "Next"}]},
        ],
    }
    assert adf_text(doc) == "Hello world. Next"
    assert adf_text("x" * 500, limit=10) == "xxxxxxxxxx…"


# -------------------------------------------------------------- Bitbucket
@pytest.mark.parametrize(
    ("url", "slug"),
    [
        ("https://bitbucket.org/ei-india-admin/billing-service", "billing-service"),
        ("https://bitbucket.org/other-ws/repo", None),
        ("https://github.com/ei-india-admin/repo", None),
        (None, None),
    ],
)
def test_repo_slug(url: str | None, slug: str | None) -> None:
    """TC-ATL-005: Only repos in the configured Bitbucket workspace are queried.

    Given: workspace ei-india-admin and various remote web URLs.
    When: repo_slug() runs.
    Then: only bitbucket.org/ei-india-admin/<slug> yields a slug.
    """
    assert repo_slug(url, "ei-india-admin") == slug


def test_review_timeline_and_durations() -> None:
    """TC-ATL-006: Turnaround uses the first approval by someone else; merges and declines are timed.

    Given: a PR opened 22 Oct 18:03, self-approved 08:00, approved by Rahul 23 Oct 10:15, merged 12:00;
           and a declined PR without activity.
    When: review_timeline() runs.
    Then: first approval is Rahul's (16h 12m after opening), merged is set, the self-approval is
          ignored, and a DECLINED PR uses updated_on as the decline time.
    """
    routes = _bb_routes()
    pr = routes[f"{BB}/repositories/ei-india-admin/billing-service/pullrequests?page=2"]["values"][
        0
    ]
    activity = routes[f"{BB}/repositories/ei-india-admin/billing-service/pullrequests/41/activity"][
        "values"
    ]
    timeline = review_timeline(pr, activity, MY_BB_UUID)
    assert timeline["approved_by"] == ["Rahul"]
    assert human_duration(timeline["created_at"], timeline["first_approved_at"]) == "16h 12m"
    assert timeline["merged_at"] is not None
    declined = review_timeline(
        {
            "state": "DECLINED",
            "created_on": "2026-10-01T00:00:00Z",
            "updated_on": "2026-10-04T05:00:00Z",
        },
        [],
        MY_BB_UUID,
    )
    assert human_duration(declined["created_at"], declined["declined_at"]) == "3d 5h"
    assert human_duration(T0, T0 + timedelta(minutes=25)) == "25m"


# ------------------------------------------------------------------- sync
def test_sync_day_stores_facts(ctx: DigestContext, clients: Clients) -> None:
    """TC-ATL-007: A day sync stores tickets, my in-window activity, In Progress tickets, missing keys and my PRs.

    Given: Jira with EIT-8403 (my comment + status move today, an old comment, a colleague's comment),
           EIT-8383, EIT-1115 In Progress, a local key CA-0214 that does not exist, and two Bitbucket PRs.
    When: sync_atlassian() runs.
    Then: tickets are stored with title/status/due/parent; only my comment and status change from today
          are events (redacted, description change ignored); In Progress = EIT-8403 and EIT-1115; CA-0214 is
          missing; both PRs are stored with turnaround; the non-Bitbucket repo is skipped.
    """
    report = sync_atlassian(ctx, DAY, clients)
    store = ctx.store
    assert report.errors == []
    assert report.missing_keys == ["CA-0214"]
    jira_http = clients.jira.http  # type: ignore[union-attr]
    first_jql = next(c[2]["jql"] for c in jira_http.calls if c[0] == "POST")  # type: ignore[attr-defined]
    assert first_jql.startswith(f'issuekey in updatedBy("{MY_JIRA_ID}", "2026-10-26 04:00"')
    assert sorted(report.in_progress) == ["EIT-1115", "EIT-8403"]
    row = store.query("SELECT * FROM tickets WHERE key = 'EIT-8403'")[0]
    assert (
        row["title"],
        row["status"],
        row["due_date"],
        row["parent_key"],
        row["assignee_is_me"],
    ) == (
        "Move billing profiles",
        "In Progress",
        "2026-10-23",
        "EIT-8000",
        1,
    )
    events = store.query(
        "SELECT kind, by_me, detail FROM ticket_events WHERE key = 'EIT-8403' ORDER BY kind, by_me"
    )
    kinds = [(e["kind"], e["by_me"]) for e in events]
    assert kinds == [("comment", 0), ("comment", 1), ("status_change", 1)]
    assert "hunter22" not in json.dumps([dict(e) for e in events])
    prs = {r["pr_id"]: r for r in store.query("SELECT * FROM pull_requests")}
    assert set(prs) == {312, 41}
    assert json.loads(prs[41]["approved_by"]) == ["Rahul"]
    assert json.loads(prs[312]["ticket_keys"]) == ["EIT-6685"]
    assert prs[312]["first_approved_at"] is None


def test_sync_isolates_failures_and_missing_tokens(ctx: DigestContext, clients: Clients) -> None:
    """TC-ATL-008: A Jira failure does not stop the Bitbucket sync, and missing tokens are skipped, not errors.

    Given: a Jira client whose /myself returns 401, a working Bitbucket client; then no clients at all.
    When: sync_atlassian() runs both times.
    Then: the first reports one jira error and still stores 2 PRs; the second reports two skips and no errors.
    """
    broken = JiraClient(FakeHttp({}), CLOUD, "https://s")

    def fail_myself() -> dict[str, Any]:
        raise AuthError(401, "x", "unauthorized")

    broken.myself = fail_myself  # type: ignore[method-assign]
    report = sync_atlassian(ctx, DAY, Clients(jira=broken, bitbucket=clients.bitbucket))
    assert len(report.errors) == 1
    assert report.pull_requests == 2
    empty = sync_atlassian(ctx, DAY, Clients(jira=None, bitbucket=None))
    assert (len(empty.skipped), empty.errors) == (2, [])


def test_make_clients_from_keychain(memory_keyring: MemoryKeyring) -> None:
    """TC-ATL-009: Clients are built only for tokens present in the Keychain, using the identity email.

    Given: only a Jira token stored, and the cloud id set in config (no network lookup).
    When: make_clients() runs.
    Then: a Jira client exists (gateway URL with the cloud id) and Bitbucket is None.
    """
    SecretStore().set("jira-token", "tok")
    built = make_clients(
        Config.model_validate(
            {
                "identity": {"author_emails": [ME]},
                "atlassian": {"jira_cloud_id": "00000000-0000-4000-8000-000000000001"},
            }
        )
    )
    assert built.jira is not None
    assert built.jira.base == JIRA
    assert built.bitbucket is None
    assert make_clients(Config()).jira is None  # no email, no client


# --------------------------------------------------------- hygiene & draft
def test_hygiene_gaps(ctx: DigestContext, clients: Clients) -> None:
    """TC-ATL-010: Ticket hygiene turns Jira facts into specific real-action gaps.

    Given: a synced day where EIT-8403 has my comment but no branch named after it, EIT-8383 has
           neither, two tickets are In Progress and CA-0214 does not exist.
    When: hygiene_gaps() runs for [EIT-8403, EIT-8383].
    Then: "EIT-8403 has no branch", "EIT-8383 has no branch", "EIT-8383 … no Activity comment",
          "2 tickets are In Progress" and "CA-0214 does not open" are reported, and no comment gap for EIT-8403.
    """
    sync_atlassian(ctx, DAY, clients)
    messages = [g.message for g in hygiene_gaps(ctx.store, DAY, ["EIT-8403", "EIT-8383"])]
    assert any(m.startswith("EIT-8403 has no branch") for m in messages)
    assert any(m.startswith("EIT-8383 has no Activity comment") for m in messages)
    assert not any(m.startswith("EIT-8403 has no Activity comment") for m in messages)
    assert any(m.startswith("2 tickets are In Progress: EIT-1115, EIT-8403") for m in messages)
    assert any("CA-0214 does not open in Jira" in m for m in messages)
    assert any(m.startswith("EIT-8383 has no due date and no parent in Jira") for m in messages)
    assert not any(m.startswith("EIT-8403 has no due date") for m in messages)
    ctx.store.upsert_branch(
        "billing-service", "EIT-8403-credits", "f" * 40, None, None, baseline=False
    )
    assert ticket_hygiene(ctx.store, DAY, ["EIT-8403"])[0].has_branch


def test_draft_uses_jira_and_bitbucket_facts(ctx: DigestContext, clients: Clients) -> None:
    """TC-ATL-011: After a sync, the draft takes titles from Jira, lists PRs with turnaround and replaces generic gaps.

    Given: a synced day.
    When: a facts-only draft is generated.
    Then: the Jira list shows "EIT-8403 – Move billing profiles"; section 8 lists PR #41 with
          "first approved … by Rahul (16h 12m after opening)" and #312 "no approval yet"; the
          generic Phase-3 PR gap and the generic R11/R12 gaps are gone, replaced by specific hygiene gaps.
    """
    sync_atlassian(ctx, DAY, clients)
    _, draft = create_draft(ctx, DAY, llm=None, now=NOW)
    text = render(draft)
    assert "- EIT-8403 – Move billing profiles" in text
    assert "PR #41" in text
    assert "by Rahul (16h 12m after opening)" in text
    assert "PR #312" in text
    assert "no approval yet, open for 21d 8h" in text
    assert "https://bitbucket.org/ei-india-admin/billing-service/pull-requests/41" in text
    gap_text = " | ".join(g.message for g in draft.gaps)
    assert "Bitbucket token" not in gap_text
    assert "checked from Jira in Phase 3" not in gap_text
    assert "tickets are In Progress" in gap_text
    statements = [s for _, s in draft.all_statements() if "PR #41" in s.text]
    assert statements
    assert statements[0].verified


# ------------------------------------------------------------------ secrets
def test_secret_store_roundtrip(memory_keyring: MemoryKeyring) -> None:
    """TC-SEC-001: Secrets are stored under the WorkDigest service, stripped, and empty values are refused.

    Given: an in-memory keyring.
    When: a token with surrounding whitespace is set, read, listed and deleted.
    Then: the stripped value is stored, known_values returns it, delete reports True then False, and "" raises.
    """
    store = SecretStore()
    store.set("jira-token", "  abc123  ")
    assert memory_keyring.data[("WorkDigest", "jira-token")] == "abc123"
    assert store.known_values() == ("abc123",)
    assert store.delete("jira-token") is True
    assert store.delete("jira-token") is False
    with pytest.raises(ValueError, match="empty"):
        store.set("jira-token", "   ")


def test_keychain_tokens_are_redacted_everywhere(
    memory_keyring: MemoryKeyring, store: Store, paths: Paths, config: Config
) -> None:
    """TC-SEC-002: A stored token is redacted from captured events even if it appears in a command.

    Given: a Jira token in the Keychain and a Bash command that echoes it.
    When: the event is ingested through the default factory.
    Then: the token never appears in the store and the known_secret rule fired.
    """
    SecretStore().set("jira-token", "ATxyzVeryLongTokenValue987")
    line = spool_line(
        claude_payload(
            "PostToolUse",
            tool_name="Bash",
            tool_use_id="t",
            tool_input={"command": "curl -u me:ATxyzVeryLongTokenValue987 https://x"},
        ),
        T0,
    )
    (paths.spool_dir / "2026-10-26.jsonl").write_text(line + "\n")
    ingest_spool(store, build_factory(config), paths.spool_dir, today=DAY)
    dump = json.dumps([dict(r) for r in store.query("SELECT payload, redaction_rules FROM events")])
    assert "ATxyzVeryLongTokenValue987" not in dump
    assert "known_secret" in dump


def test_cli_secrets_and_atlassian_check(paths: Paths, memory_keyring: MemoryKeyring) -> None:
    """TC-SEC-003: `secrets set` reads hidden input, `secrets status` never prints values, `atlassian check` reports missing tokens.

    Given: an empty keyring.
    When: `secrets set jira` is given a token on stdin, then `secrets status`, `secrets set nope`, and `atlassian check`.
    Then: the token is stored, status shows stored/missing without the value, an unknown name exits 2,
          and check exits 1 naming the missing Bitbucket token.
    """
    runner = CliRunner()
    result = runner.invoke(cli.app, ["secrets", "set", "jira"], input="tok-123\n")
    assert result.exit_code == 0, result.output
    assert "tok-123" not in result.output
    status = runner.invoke(cli.app, ["secrets", "status"])
    assert "jira-token: stored" in status.output
    assert "bitbucket-token: missing" in status.output
    assert "tok-123" not in status.output
    assert runner.invoke(cli.app, ["secrets", "set", "nope"], input="x\n").exit_code == 2
    check = runner.invoke(cli.app, ["atlassian", "check"])
    assert check.exit_code == 1
    assert "Bitbucket: no token" in check.output
    assert runner.invoke(cli.app, ["secrets", "delete", "jira"]).exit_code == 0


def test_in_progress_rule_counts_status_name_not_category(ctx: DigestContext) -> None:
    """TC-ATL-012: "Only one In Progress" counts the status named In Progress, not Review/Testing (live finding).

    Given: a snapshot like the real 23 Oct one: 2 tickets In Progress, 2 in Review, 1 In Testing.
    When: hygiene_gaps() runs.
    Then: the rule gap names only the 2 In Progress tickets; Review/Testing ones get a separate info line.
    """
    store = ctx.store
    store.conn.execute(
        "INSERT INTO tickets (key, title, status, url, synced_at) VALUES ('EIT-1', 't', 'In Progress', 'u', 'n')"
    )
    for key, status in (
        ("EIT-1", "In Progress"),
        ("EIT-2", "In Progress"),
        ("EIT-3", "Review"),
        ("EIT-4", "Review"),
        ("EIT-5", "In Testing"),
    ):
        store.conn.execute(
            "INSERT INTO in_progress_snapshot VALUES (?, ?, ?)", (DAY.isoformat(), key, status)
        )
    gaps = hygiene_gaps(store, DAY, [])
    rule = [g.message for g in gaps if g.kind == "real_action" and g.ref == "R12-one-in-progress"]
    assert rule == ["2 tickets are In Progress: EIT-1, EIT-2; keep one and move the others"]
    assert any(
        g.kind == "input" and g.message.startswith("3 more of your tickets are in Review/Testing")
        for g in gaps
    )


def test_cloud_id_is_looked_up_from_the_site() -> None:
    """TC-ATL-013: Without a configured cloud id, it is read once from the site's public tenant_info.

    Given: a fake site answering tenant_info with a cloud id, then one answering without it.
    When: resolve_cloud_id is called twice for the first site and once for the second.
    Then: the id is returned and cached (one request), and a missing id is an HttpError.
    """
    import io
    import json

    from workdigest.integrations import jira as jira_module
    from workdigest.integrations.http import HttpError
    from workdigest.integrations.jira import resolve_cloud_id

    calls: list[str] = []

    class _Resp(io.BytesIO):
        status = 200

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *_: object) -> None:
            self.close()

    def opener(request: Any, timeout: float) -> _Resp:
        calls.append(request.full_url)
        body = {"cloudId": "abc-123"} if "good" in request.full_url else {}
        return _Resp(json.dumps(body).encode())

    jira_module._CLOUD_IDS.clear()
    assert resolve_cloud_id("https://good.atlassian.net/", opener=opener) == "abc-123"
    assert resolve_cloud_id("https://good.atlassian.net", opener=opener) == "abc-123"
    assert calls == ["https://good.atlassian.net/_edge/tenant_info"]
    with pytest.raises(HttpError):
        resolve_cloud_id("https://empty.atlassian.net", opener=opener)
