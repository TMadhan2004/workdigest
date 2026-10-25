"""Pull one work day's Jira and Bitbucket facts into SQLite, then check ticket hygiene.

Jira:
* tickets I updated during the day window (``updatedBy(currentUser(), …)``), plus every
  ticket key the recorder saw that day. Missing or inaccessible keys are noted, which is
  how "CA-0214 doesn't open" becomes visible;
* my comments and status changes inside the window (comment text is redacted and capped);
* every ticket currently In Progress and assigned to me (for "only one In Progress").

Bitbucket:
* my PRs in each configured repo of the workspace, updated in the backfill window or still
  open, with when they were opened, first approved by someone else, declined or merged.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from workdigest.clock import day_window, local_tz, now_local, parse_iso, work_date
from workdigest.config import Config
from workdigest.ingest.context import TicketMatcher
from workdigest.integrations.bitbucket import BitbucketClient, repo_slug, review_timeline
from workdigest.integrations.http import BasicAuthClient, HttpError
from workdigest.integrations.jira import JiraClient, adf_text, resolve_cloud_id
from workdigest.models import canonical_json, stable_hash
from workdigest.redact.redactor import Redactor
from workdigest.secrets import SecretStore
from workdigest.store.db import Store

COMMENT_CHARS = 300


@dataclass(slots=True)
class SyncReport:
    """What a sync did; errors never abort the other half."""

    jira_tickets: int = 0
    jira_events: int = 0
    missing_keys: list[str] = field(default_factory=list)
    in_progress: list[str] = field(default_factory=list)
    pull_requests: int = 0
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Clients:
    """Authenticated clients (either may be None when its token is missing)."""

    jira: JiraClient | None
    bitbucket: BitbucketClient | None


def make_clients(config: Config, secrets: SecretStore | None = None) -> Clients:
    """Build clients from Keychain tokens; a missing token disables that side."""
    store = secrets or SecretStore()
    email = config.atlassian_email
    settings = config.atlassian
    jira_token = store.get("jira-token")
    bitbucket_token = store.get("bitbucket-token")
    jira = None
    if jira_token and email and settings.enabled:
        try:
            cloud_id = settings.jira_cloud_id or resolve_cloud_id(settings.jira_site)
        except HttpError:
            cloud_id = ""  # site unreachable: Jira is skipped this time and retried next sync
        if cloud_id:
            jira = JiraClient(
                BasicAuthClient(email, jira_token, timeout_s=settings.timeout_s),
                cloud_id,
                settings.jira_site,
            )
    bitbucket = (
        BitbucketClient(
            BasicAuthClient(email, bitbucket_token, timeout_s=settings.timeout_s),
            settings.bitbucket_workspace,
        )
        if bitbucket_token and email and settings.enabled
        else None
    )
    return Clients(jira=jira, bitbucket=bitbucket)


def sync_day(
    store: Store, config: Config, clients: Clients, day: date, redactor: Redactor
) -> SyncReport:
    """Sync Jira and Bitbucket facts for ``day``."""
    report = SyncReport()
    if clients.jira is None:
        report.skipped.append("jira (no token in Keychain)")
    else:
        try:
            _sync_jira(store, config, clients.jira, day, redactor=redactor, report=report)
        except HttpError as exc:
            report.errors.append(f"jira: {exc}")
            store.log_error("atlassian.jira", str(exc))
    if clients.bitbucket is None:
        report.skipped.append("bitbucket (no token in Keychain)")
    else:
        try:
            _sync_bitbucket(store, config, clients.bitbucket, day, report)
        except HttpError as exc:
            report.errors.append(f"bitbucket: {exc}")
            store.log_error("atlassian.bitbucket", str(exc))
    return report


# ---------------------------------------------------------------------- Jira
def _local_ticket_keys(store: Store, day: date) -> list[str]:
    keys: dict[str, None] = {}
    for row in store.query(
        "SELECT ticket_keys FROM events WHERE work_date = ?", (day.isoformat(),)
    ):
        keys.update(dict.fromkeys(json.loads(row["ticket_keys"])))
    for row in store.query(
        "SELECT ticket_keys FROM commits WHERE work_date = ?", (day.isoformat(),)
    ):
        keys.update(dict.fromkeys(json.loads(row["ticket_keys"])))
    return list(keys)


def _sync_jira(
    store: Store,
    config: Config,
    jira: JiraClient,
    day: date,
    *,
    redactor: Redactor,
    report: SyncReport,
) -> None:
    me = jira.myself()
    my_id = str(me.get("accountId") or "")
    start, end = day_window(day, config.day.boundary_hour)
    # updatedBy() rejects a nested currentUser() call (live 400, 24 Oct 2026): pass the account id.
    jql = f'issuekey in updatedBy("{my_id}", "{start:%Y-%m-%d %H:%M}", "{end:%Y-%m-%d %H:%M}")'
    issues = {issue["key"]: issue for issue in jira.search(jql)}
    for key in _local_ticket_keys(store, day):
        if key not in issues:
            fetched = jira.issue(key)
            if fetched is None:
                report.missing_keys.append(key)
            else:
                issues[key] = fetched
    with store.transaction():
        store.conn.execute("DELETE FROM ticket_missing WHERE synced_on = ?", (day.isoformat(),))
        for key in report.missing_keys:
            store.conn.execute(
                "INSERT OR IGNORE INTO ticket_missing VALUES (?, ?)", (key, day.isoformat())
            )
        for key, issue in issues.items():
            _upsert_ticket(store, jira, issue, my_id)
            report.jira_tickets += 1
            report.jira_events += _sync_ticket_events(
                store,
                jira,
                key,
                my_id=my_id,
                start=start,
                end=end,
                config=config,
                redactor=redactor,
            )
        in_progress = list(
            jira.search(
                'assignee = currentUser() AND statusCategory = "In Progress" ORDER BY updated DESC'
            )
        )
        store.conn.execute(
            "DELETE FROM in_progress_snapshot WHERE synced_on = ?", (day.isoformat(),)
        )
        for issue in in_progress:
            if issue["key"] not in issues:  # keep the fuller record fetched above
                _upsert_ticket(store, jira, issue, my_id)
            status = ((issue.get("fields") or {}).get("status") or {}).get("name") or "In Progress"
            store.conn.execute(
                "INSERT OR REPLACE INTO in_progress_snapshot VALUES (?, ?, ?)",
                (day.isoformat(), issue["key"], status),
            )
            report.in_progress.append(issue["key"])


def _upsert_ticket(store: Store, jira: JiraClient, issue: dict[str, object], my_id: str) -> None:
    fields = issue.get("fields") or {}
    assert isinstance(fields, dict)  # noqa: S101 - Jira always returns an object here
    status = fields.get("status") or {}
    category = (
        (status.get("statusCategory") or {}).get("name") if isinstance(status, dict) else None
    )
    parent = fields.get("parent") or {}
    assignee = fields.get("assignee") or {}
    key = str(issue["key"])
    store.conn.execute(
        """
        INSERT INTO tickets (key, title, status, status_category, issue_type, assignee_is_me, due_date, parent_key, url, updated_at, synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (key) DO UPDATE SET
            title = excluded.title, status = excluded.status, status_category = excluded.status_category,
            issue_type = excluded.issue_type, assignee_is_me = excluded.assignee_is_me, due_date = excluded.due_date,
            parent_key = excluded.parent_key, updated_at = excluded.updated_at, synced_at = excluded.synced_at
        """,
        (
            key,
            str(fields.get("summary") or ""),
            status.get("name") if isinstance(status, dict) else None,
            category,
            ((fields.get("issuetype") or {}).get("name"))
            if isinstance(fields.get("issuetype"), dict)
            else None,
            int(isinstance(assignee, dict) and assignee.get("accountId") == my_id),
            fields.get("duedate"),
            parent.get("key") if isinstance(parent, dict) else None,
            jira.browse_url(key),
            fields.get("updated"),
            now_local().isoformat(),
        ),
    )


def _sync_ticket_events(
    store: Store,
    jira: JiraClient,
    key: str,
    *,
    my_id: str,
    start: datetime,
    end: datetime,
    config: Config,
    redactor: Redactor,
) -> int:
    added = 0
    for comment in jira.comments(key):
        created = parse_iso(str(comment.get("created") or ""))
        if created is None or not (start <= created < end):
            continue
        author = (comment.get("author") or {}).get("accountId")
        text = redactor.redact_text(adf_text(comment.get("body"), COMMENT_CHARS)).value
        added += _insert_ticket_event(
            store,
            key,
            "comment",
            source_id=str(comment.get("id")),
            ts=created,
            by_me=author == my_id,
            detail={"excerpt": text},
            config=config,
        )
    for history in jira.changelog(key):
        created = parse_iso(str(history.get("created") or ""))
        if created is None or not (start <= created < end):
            continue
        by_me = (history.get("author") or {}).get("accountId") == my_id
        for item in history.get("items", []):
            field_name = str(item.get("field") or "")
            kind = "status_change" if field_name.lower() == "status" else "field_change"
            if kind == "field_change" and field_name.lower() not in {
                "duedate",
                "parent",
                "assignee",
                "issuetype",
            }:
                continue
            detail = {
                "field": field_name,
                "from": item.get("fromString"),
                "to": item.get("toString"),
            }
            added += _insert_ticket_event(
                store,
                key,
                kind,
                source_id=f"{history.get('id')}:{field_name}",
                ts=created,
                by_me=by_me,
                detail=detail,
                config=config,
            )
    return added


def _insert_ticket_event(
    store: Store,
    key: str,
    kind: str,
    *,
    source_id: str,
    ts: datetime,
    by_me: bool,
    detail: dict[str, object],
    config: Config,
) -> int:
    cursor = store.conn.execute(
        "INSERT OR IGNORE INTO ticket_events (event_id, key, kind, ts, work_date, by_me, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            stable_hash(key, kind, source_id),
            key,
            kind,
            ts.astimezone(local_tz()).isoformat(),
            work_date(ts, config.day.boundary_hour).isoformat(),
            int(by_me),
            canonical_json(detail),
        ),
    )
    return cursor.rowcount


# ----------------------------------------------------------------- Bitbucket
def _sync_bitbucket(
    store: Store, config: Config, bitbucket: BitbucketClient, day: date, report: SyncReport
) -> None:
    me = bitbucket.user()
    my_uuid = str(me.get("uuid") or "")
    since = day_window(day, config.day.boundary_hour)[0] - timedelta(
        days=config.sources.backfill_days + 5
    )
    tickets = TicketMatcher(config.jira.project_keys)
    repos = store.query("SELECT name, web_url FROM repos ORDER BY name")
    with store.transaction():
        for repo in repos:
            slug = repo_slug(repo["web_url"], config.atlassian.bitbucket_workspace)
            if slug is None:
                continue
            for pr in bitbucket.pull_requests(slug, my_uuid, since):
                timeline = review_timeline(pr, bitbucket.activity(slug, int(pr["id"])), my_uuid)
                source = ((pr.get("source") or {}).get("branch") or {}).get("name")
                destination = ((pr.get("destination") or {}).get("branch") or {}).get("name")
                url = ((pr.get("links") or {}).get("html") or {}).get("href") or ""
                title = str(pr.get("title") or "")
                store.conn.execute(
                    """
                    INSERT OR REPLACE INTO pull_requests
                        (repo, pr_id, title, url, source_branch, dest_branch, state, created_at, updated_at,
                         first_approved_at, approved_by, declined_at, merged_at, ticket_keys, synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        repo["name"],
                        int(pr["id"]),
                        title,
                        url,
                        source,
                        destination,
                        str(pr.get("state") or "OPEN"),
                        _iso(timeline["created_at"]),
                        _iso(timeline["updated_at"]),
                        _iso(timeline["first_approved_at"]),
                        canonical_json(timeline["approved_by"]),
                        _iso(timeline["declined_at"]),
                        _iso(timeline["merged_at"]),
                        canonical_json(tickets.find(title, source)),
                        now_local().isoformat(),
                    ),
                )
                report.pull_requests += 1


def _iso(value: object) -> str | None:
    return value.astimezone(local_tz()).isoformat() if isinstance(value, datetime) else None


# ------------------------------------------------------------------- hygiene
@dataclass(frozen=True, slots=True)
class TicketHygiene:
    """Per-ticket checks behind standing rules R11 and R12."""

    key: str
    has_branch: bool
    commented_today: bool
    status: str | None
    has_due_date: bool | None = None  # None when Jira has not been synced for this key
    has_parent: bool | None = None


def ticket_hygiene(store: Store, day: date, keys: list[str]) -> list[TicketHygiene]:
    """For each ticket worked on: a branch named after it, and my comment today."""
    branches = [r["name"] for r in store.query("SELECT name FROM branches")]
    commit_branches = [
        b for r in store.query("SELECT branches FROM commits") for b in json.loads(r["branches"])
    ]
    names = [n.upper() for n in branches + commit_branches]
    out = []
    for key in keys:
        pattern = re.compile(rf"(?<![A-Z0-9]){re.escape(key.upper())}(?!\d)")
        has_branch = any(pattern.search(name) for name in names)
        commented = bool(
            store.scalar(
                "SELECT 1 FROM ticket_events WHERE key = ? AND work_date = ? AND kind = 'comment' AND by_me = 1",
                (key, day.isoformat()),
            )
        )
        rows = store.query("SELECT status, due_date, parent_key FROM tickets WHERE key = ?", (key,))
        row = rows[0] if rows else None
        out.append(
            TicketHygiene(
                key,
                has_branch,
                commented,
                row["status"] if row else None,
                has_due_date=bool(row["due_date"]) if row else None,
                has_parent=bool(row["parent_key"]) if row else None,
            )
        )
    return out


def in_progress_on(store: Store, day: date) -> list[tuple[str, str]] | None:
    """Tickets In Progress and assigned to me at the last sync of ``day`` (None if never synced)."""
    rows = store.query(
        "SELECT key, status FROM in_progress_snapshot WHERE synced_on = ? ORDER BY key",
        (day.isoformat(),),
    )
    if not rows and not store.scalar("SELECT 1 FROM tickets LIMIT 1"):
        return None
    return [(r["key"], r["status"]) for r in rows]


def missing_on(store: Store, day: date) -> list[str]:
    """Ticket keys Jira could not find at the last sync of ``day``."""
    return [
        r["key"]
        for r in store.query(
            "SELECT key FROM ticket_missing WHERE synced_on = ? ORDER BY key", (day.isoformat(),)
        )
    ]


def synced(store: Store) -> bool:
    """Whether Jira data has ever been synced."""
    return bool(store.scalar("SELECT 1 FROM tickets LIMIT 1"))


def parse_ts(value: str | None) -> datetime | None:
    """Stored ISO timestamp → datetime."""
    return parse_iso(value) if value else None
