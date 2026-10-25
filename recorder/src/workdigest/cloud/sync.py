"""Two-way sync between the local SQLite store and Supabase.

**Push (Mac → cloud).** Each cloud table has a builder that reads local rows for the last
``push_days`` days. A row is uploaded only when its content hash changed since the last push,
so a sync is idempotent and cheap. ``plan_push`` returns exactly what would be sent, and
``digest cloud push --dry-run`` prints it. Only redacted data exists locally, and no raw
transcripts or diffs are ever built into a row.

**Pull (cloud → Mac).** Changes made in the app since the last pull are applied locally:
* ask status, evidence and reason;
* reply items confirmed, dismissed or relabelled;
* a manager reply pasted or shared in the app (parsed on the Mac);
* a digest edited in the app (kept as an ``edited`` version).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Protocol

from workdigest.clock import now_local
from workdigest.config import Config
from workdigest.digest.my_rules import RuleFilters
from workdigest.digest.repository import DigestRepository
from workdigest.models import canonical_json, stable_hash
from workdigest.store.db import Store

PUSH_ORDER = (
    "days",
    "session_summaries",
    "tickets",
    "pull_requests",
    "digests",
    "digest_statements",
    "evidence",
    "manager_replies",
    "reply_items",
    "asks",
    "standing_rules",
)
PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "days": ("user_id", "work_date"),
    "session_summaries": ("user_id", "session_key", "work_date"),
    "tickets": ("user_id", "key"),
    "pull_requests": ("user_id", "repo", "pr_id"),
    "digests": ("user_id", "digest_id"),
    "digest_statements": ("user_id", "digest_id", "section_key", "idx"),
    "evidence": ("user_id", "digest_id", "evidence_id"),
    "manager_replies": ("user_id", "reply_id"),
    "reply_items": ("user_id", "item_id"),
    "asks": ("user_id", "ask_id"),
    "standing_rules": ("user_id", "rule_id"),
}
BATCH = 200
PULL_MARGIN = timedelta(seconds=5)


class Tables(Protocol):
    """The cloud operations sync needs (``cloud.client.Rest`` or a test fake)."""

    @property
    def user_id(self) -> str:
        """Signed-in user."""

    def select(self, table: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        """Rows matching PostgREST filters."""

    def upsert(self, table: str, rows: list[dict[str, Any]], on_conflict: str) -> None:
        """Insert or update by key."""

    def insert(self, table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Insert rows."""

    def update(
        self, table: str, filters: dict[str, str], values: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Update matching rows."""


# ------------------------------------------------------------------- builders
def _days_in_window(store: Store, since: date) -> list[str]:
    rows = store.query(
        "SELECT DISTINCT work_date FROM events WHERE work_date >= ? "
        "UNION SELECT work_date FROM digests WHERE work_date >= ? "
        "UNION SELECT work_date FROM day_state WHERE work_date >= ? ORDER BY 1",
        (since.isoformat(), since.isoformat(), since.isoformat()),
    )
    return [r[0] for r in rows]


def _build_days(store: Store, config: Config, since: date) -> Iterator[dict[str, Any]]:
    for day in _days_in_window(store, since):
        state = store.query("SELECT * FROM day_state WHERE work_date = ?", (day,))
        sessions = store.scalar(
            "SELECT COUNT(DISTINCT session_key) FROM events WHERE work_date = ? AND session_key IS NOT NULL "
            "AND session_key NOT IN (SELECT session_key FROM hidden_sessions)",
            (day,),
        )
        commits = store.scalar("SELECT COUNT(*) FROM commits WHERE work_date = ?", (day,))
        row = state[0] if state else None
        out: dict[str, Any] = {
            "work_date": day,
            "counts": {"sessions": int(sessions or 0), "commits": int(commits or 0)},
        }
        # Rating, project, team and closing can also be set in the app. Only send values the Mac
        # actually has: an upsert with nulls would wipe what the app wrote.
        if row and row["closed_at"]:
            out.update(status="closed", closed_at=row["closed_at"])
        if row and row["rating"] is not None:
            out["rating"] = row["rating"]
        if row and row["project"]:
            out["project"] = row["project"]
        if row and json.loads(row["team_members"]):
            out["team_members"] = json.loads(row["team_members"])
        yield out


def _build_session_summaries(store: Store, config: Config, since: date) -> Iterator[dict[str, Any]]:
    if not config.cloud.sync_session_summaries:
        return
    rows = store.query(
        """
        SELECT ss.session_key, ss.work_date, ss.summary, s.source, s.title,
               MIN(e.ts) AS first_ts, MAX(e.ts) AS last_ts,
               json_group_array(DISTINCT e.repo) AS repos
        FROM session_summaries ss
        JOIN sessions s ON s.session_key = ss.session_key
        JOIN events e ON e.session_key = ss.session_key AND e.work_date = ss.work_date
        WHERE ss.work_date >= ?
        GROUP BY ss.session_key, ss.work_date
        """,
        (since.isoformat(),),
    )
    for r in rows:
        yield {
            "session_key": r["session_key"],
            "work_date": r["work_date"],
            "source": r["source"],
            "title": r["title"],
            "repos": [x for x in json.loads(r["repos"]) if x],
            "first_ts": r["first_ts"],
            "last_ts": r["last_ts"],
            "summary": json.loads(r["summary"]),
        }


def _build_tickets(store: Store, config: Config, since: date) -> Iterator[dict[str, Any]]:
    for r in store.query("SELECT * FROM tickets"):
        yield {
            "key": r["key"],
            "title": r["title"],
            "status": r["status"],
            "status_category": r["status_category"],
            "due_date": r["due_date"],
            "parent_key": r["parent_key"],
            "url": r["url"],
        }


def _build_pull_requests(store: Store, config: Config, since: date) -> Iterator[dict[str, Any]]:
    for r in store.query("SELECT * FROM pull_requests"):
        yield {
            "repo": r["repo"],
            "pr_id": r["pr_id"],
            "title": r["title"],
            "url": r["url"],
            "source_branch": r["source_branch"],
            "dest_branch": r["dest_branch"],
            "state": r["state"],
            "opened_at": r["created_at"],
            "first_approved_at": r["first_approved_at"],
            "approved_by": json.loads(r["approved_by"]),
            "declined_at": r["declined_at"],
            "merged_at": r["merged_at"],
            "ticket_keys": json.loads(r["ticket_keys"] or "[]"),
        }


def _local_digests(store: Store, since: date) -> list[Any]:
    return store.query(
        "SELECT * FROM digests WHERE work_date >= ? ORDER BY created_at", (since.isoformat(),)
    )


def _build_digests(store: Store, config: Config, since: date) -> Iterator[dict[str, Any]]:
    for r in _local_digests(store, since):
        structure = json.loads(r["structure"])
        structure.pop("evidence", None)  # evidence goes to its own table (or stays local)
        yield {
            "digest_id": r["digest_id"],
            "work_date": r["work_date"],
            "origin": r["origin"],
            "status": r["status"],
            "template_version": r["template_version"],
            "text": r["text"],
            "structure": structure,
            "gaps": json.loads(r["gaps"]),
            "provider": structure.get("provider"),
            "created_at": r["created_at"],
        }


def _build_digest_statements(store: Store, config: Config, since: date) -> Iterator[dict[str, Any]]:
    ids = [r["digest_id"] for r in _local_digests(store, since)]
    for digest_id in ids:
        for r in store.query(
            "SELECT * FROM digest_statements WHERE digest_id = ? ORDER BY section_key, idx",
            (digest_id,),
        ):
            yield {
                "digest_id": digest_id,
                "section_key": r["section_key"],
                "idx": r["idx"],
                "text": r["text"],
                "verified": bool(r["verified"]),
                "evidence_ids": json.loads(r["evidence_ids"]),
                "problems": json.loads(r["problems"]),
            }


def _build_evidence(store: Store, config: Config, since: date) -> Iterator[dict[str, Any]]:
    if not config.cloud.sync_evidence:
        return
    for r in _local_digests(store, since):
        for item in (json.loads(r["structure"]).get("evidence") or {}).values():
            yield {
                "digest_id": r["digest_id"],
                "evidence_id": item["id"],
                "kind": item["kind"],
                "strength": item["strength"],
                "text": item["text"],
                "refs": item.get("refs") or {},
            }


def _build_manager_replies(store: Store, config: Config, since: date) -> Iterator[dict[str, Any]]:
    if not config.cloud.sync_replies:
        return
    for r in store.query("SELECT * FROM manager_replies"):
        yield {
            "reply_id": r["reply_id"],
            "for_work_date": r["for_work_date"],
            "raw_text": r["raw_text"],
            "processed": True,
            "received_at": r["received_at"],
        }


def _build_reply_items(store: Store, config: Config, since: date) -> Iterator[dict[str, Any]]:
    if not config.cloud.sync_replies:
        return
    for r in store.query("SELECT * FROM reply_items"):
        yield {
            "item_id": r["item_id"],
            "reply_id": r["reply_id"],
            "idx": r["idx"],
            "type": r["type"],
            "heading": r["heading"],
            "text": r["text"],
            "related_tickets": json.loads(r["related_tickets"]),
            "due_date": r["due_date"],
            "status": r["status"],
            "method": r["method"],
            "updated_by": "mac",
        }


def _build_asks(store: Store, config: Config, since: date) -> Iterator[dict[str, Any]]:
    for ask in DigestRepository(store).asks():
        yield {
            "ask_id": ask.ask_id,
            "text": ask.text,
            "kind": ask.kind,
            "related_tickets": ask.related_tickets,
            "status": ask.status,
            "needs_real_action": ask.needs_real_action,
            "evidence": ask.evidence,
            "reason": ask.reason,
            "opened_on": ask.opened_on,
            "closed_on": ask.closed_on,
            "times_raised": ask.times_raised,
            "updated_by": "mac",
        }


def _build_standing_rules(store: Store, config: Config, since: date) -> Iterator[dict[str, Any]]:
    for rule in DigestRepository(store).rules(active_only=False):
        yield {
            "rule_id": rule.rule_id,
            "text": rule.text,
            "check_kind": rule.check_kind,
            "check_params": rule.check_params,
            "source": rule.source,
            "active": rule.active,
        }


BUILDERS: dict[str, Callable[[Store, Config, date], Iterator[dict[str, Any]]]] = {
    "days": _build_days,
    "session_summaries": _build_session_summaries,
    "tickets": _build_tickets,
    "pull_requests": _build_pull_requests,
    "digests": _build_digests,
    "digest_statements": _build_digest_statements,
    "evidence": _build_evidence,
    "manager_replies": _build_manager_replies,
    "reply_items": _build_reply_items,
    "asks": _build_asks,
    "standing_rules": _build_standing_rules,
}


# ----------------------------------------------------------------------- push
@dataclass(slots=True)
class PushPlan:
    """Rows that differ from what was last pushed, per table."""

    changes: dict[str, list[tuple[str, str, dict[str, Any]]]] = field(
        default_factory=dict
    )  # key, hash, row

    def counts(self) -> dict[str, int]:
        """Number of changed rows per table."""
        return {table: len(rows) for table, rows in self.changes.items() if rows}

    @property
    def total(self) -> int:
        """Total changed rows."""
        return sum(len(rows) for rows in self.changes.values())


def plan_push(store: Store, config: Config, today: date | None = None) -> PushPlan:
    """Work out what a push would upload, without sending anything."""
    since = (today or now_local().date()) - timedelta(days=config.cloud.push_days)
    plan = PushPlan()
    for table in PUSH_ORDER:
        keys = [k for k in PRIMARY_KEYS[table] if k != "user_id"]
        pushed = {
            r["row_key"]: r["row_hash"]
            for r in store.query(
                "SELECT row_key, row_hash FROM cloud_sync_state WHERE table_name = ?", (table,)
            )
        }
        changes = []
        for row in BUILDERS[table](store, config, since):
            row_key = "|".join(str(row[k]) for k in keys)
            row_hash = stable_hash(canonical_json(row))
            if pushed.get(row_key) != row_hash:
                changes.append((row_key, row_hash, row))
        plan.changes[table] = changes
    return plan


def ensure_template(tables: Tables, template: dict[str, Any], version: str) -> bool:
    """Upload the Mac's template as the active one when the cloud has none (so the app can edit it)."""
    if tables.select("templates", {"active": "eq.true", "limit": "1"}):
        return False
    tables.upsert(
        "templates",
        [{"user_id": tables.user_id, "version": version, "body": template, "active": True}],
        "user_id,version",
    )
    return True


def push(store: Store, config: Config, tables: Tables, today: date | None = None) -> dict[str, int]:
    """Upload changed rows (parents before children) and record what was sent."""
    plan = plan_push(store, config, today)
    user_id = tables.user_id
    stamp = now_local().isoformat()
    for table in PUSH_ORDER:
        rows = plan.changes.get(table, [])
        conflict = ",".join(PRIMARY_KEYS[table])
        for start in range(0, len(rows), BATCH):
            batch = rows[start : start + BATCH]
            tables.upsert(table, [{**row, "user_id": user_id} for _, _, row in batch], conflict)
            with store.transaction():
                store.conn.executemany(
                    "INSERT OR REPLACE INTO cloud_sync_state VALUES (?, ?, ?, ?)",
                    [(table, key, row_hash, stamp) for key, row_hash, _ in batch],
                )
    return plan.counts()


# ----------------------------------------------------------------------- pull
@dataclass(slots=True)
class PullReport:
    """What a pull applied locally."""

    asks: int = 0
    days: int = 0
    settings: bool = False
    template: bool = False
    reply_items: int = 0
    replies_confirmed: int = 0
    new_replies: list[str] = field(default_factory=list)
    edited_digests: int = 0


def _watermark(store: Store) -> str:
    value = store.scalar("SELECT value FROM cloud_meta WHERE key = 'pulled_at'")
    return str(value) if value else "1970-01-01T00:00:00+00:00"


def pull(
    store: Store,
    config: Config,
    tables: Tables,
    on_new_reply: Callable[[str, str, str | None], None],
    overrides_dir: Path | None = None,
) -> PullReport:
    """Apply app-side changes made since the last pull (settings/template go to ``overrides_dir``)."""
    report = PullReport()
    since = _watermark(store)
    started = (now_local() - PULL_MARGIN).isoformat()
    repo = DigestRepository(store)

    with store.transaction():
        for row in tables.select("asks", {"updated_by": "eq.app", "updated_at": f"gt.{since}"}):
            cursor = store.conn.execute(
                "UPDATE asks SET status = ?, evidence = ?, reason = ?, closed_on = ? WHERE ask_id = ?",
                (
                    row["status"],
                    row.get("evidence"),
                    row.get("reason"),
                    row.get("closed_on"),
                    row["ask_id"],
                ),
            )
            report.asks += cursor.rowcount

        for row in tables.select("days", {"updated_at": f"gt.{since}"}):
            if row.get("rating") is None and not row.get("team_members") and not row.get("project"):
                continue
            store.conn.execute(
                """
                INSERT INTO day_state (work_date, rating, project, team_members) VALUES (?, ?, ?, ?)
                ON CONFLICT (work_date) DO UPDATE SET
                    rating = COALESCE(excluded.rating, day_state.rating),
                    project = COALESCE(excluded.project, day_state.project),
                    team_members = CASE WHEN excluded.team_members != '[]' THEN excluded.team_members ELSE day_state.team_members END
                """,
                (
                    row["work_date"],
                    row.get("rating"),
                    row.get("project"),
                    json.dumps(row.get("team_members") or []),
                ),
            )
            report.days += 1

        touched_replies: set[str] = set()
        for row in tables.select(
            "reply_items", {"updated_by": "eq.app", "updated_at": f"gt.{since}"}
        ):
            cursor = store.conn.execute(
                "UPDATE reply_items SET status = ?, type = ?, text = ?, method = 'manual' WHERE item_id = ?",
                (row["status"], row["type"], row["text"], row["item_id"]),
            )
            if cursor.rowcount:
                report.reply_items += 1
                touched_replies.add(row["reply_id"])
        for reply_id in sorted(touched_replies):
            statuses = {r["status"] for r in repo.reply_items(reply_id)}
            if "proposed" not in statuses and statuses & {"confirmed"}:
                day = store.scalar(
                    "SELECT for_work_date FROM manager_replies WHERE reply_id = ?", (reply_id,)
                )
                repo.confirm_reply(reply_id, date.fromisoformat(day) if day else now_local().date())
                report.replies_confirmed += 1

        for row in tables.select("digests", {"origin": "eq.edited", "updated_at": f"gt.{since}"}):
            repo.save_digest(
                date.fromisoformat(row["work_date"]),
                row["text"],
                origin="edited",
                status=row["status"],
                template_version=row.get("template_version"),
                structure=row.get("structure") or {},
                gaps=row.get("gaps") or [],
            )
            report.edited_digests += 1

    for row in tables.select("manager_replies", {"source": "eq.app", "processed": "eq.false"}):
        on_new_reply(row["reply_id"], row["raw_text"], row.get("for_work_date"))
        tables.update("manager_replies", {"reply_id": f"eq.{row['reply_id']}"}, {"processed": True})
        report.new_replies.append(row["reply_id"])

    if overrides_dir is not None:
        report.settings, report.template = _pull_settings(tables, overrides_dir)
    store.conn.execute("INSERT OR REPLACE INTO cloud_meta VALUES ('pulled_at', ?)", (started,))
    return report


SETTINGS_FILE = "cloud_settings.json"
TEMPLATE_FILE = "template.json"


def _pull_settings(tables: Tables, home: Path) -> tuple[bool, bool]:
    """Write the app's provider/transmit choices and active template where the Mac reads them."""
    wrote_settings = wrote_template = False
    rows = tables.select("settings", {"limit": "1"})
    if rows:
        settings = rows[0]
        overrides = {
            "provider": settings.get("provider"),
            "transmit": settings.get("transmit") or {},
        }
        (home / SETTINGS_FILE).write_text(json.dumps(overrides, indent=2), encoding="utf-8")
        wrote_settings = True
    templates = tables.select("templates", {"active": "eq.true", "limit": "1"})
    if templates:
        (home / TEMPLATE_FILE).write_text(
            json.dumps(templates[0]["body"], indent=2), encoding="utf-8"
        )
        wrote_template = True
    return wrote_settings, wrote_template


@dataclass(slots=True)
class UserStateReport:
    """Hidden sessions and My rules mirrored from the app."""

    hidden_sessions: int = 0
    my_rules: int = 0
    rules_read: list[str] = field(default_factory=list)


def sync_user_state(
    store: Store, tables: Tables, interpret_rule: Callable[[str], RuleFilters]
) -> UserStateReport:
    """Mirror hidden sessions and My rules from the cloud, and read rules that are new or edited.

    Both lists are small, so they are copied whole every tick: a restore (row deleted in the
    app) or a deleted rule simply disappears, with no deletion events to miss. A rule in the
    "parsing" state is interpreted here and sent back as "needs_review" for the user to confirm.
    """
    report = UserStateReport()
    hidden = tables.select("hidden_sessions", {"select": "session_key,hidden_at"})
    # Priya's rules cleared (or restored) in the app: only the on/off flag is the app's to set.
    managed = [
        r
        for r in tables.select(
            "standing_rules", {"updated_by": "eq.app", "select": "rule_id,source,active"}
        )
        if r.get("source") != "mine"
    ]
    rules = tables.select("standing_rules", {"source": "eq.mine"})
    for rule in rules:
        if rule.get("status") != "parsing":
            continue
        filters = interpret_rule(str(rule["text"]))
        # Only if the rule is unchanged since it was read: an edit made meanwhile (new text,
        # new updated_at, still "parsing") must be read again, not overwritten.
        guard = {"rule_id": f"eq.{rule['rule_id']}", "status": "eq.parsing"}
        if rule.get("updated_at"):
            guard["updated_at"] = f"eq.{rule['updated_at']}"
        changed = tables.update(
            "standing_rules",
            guard,
            {
                "check_params": filters.model_dump(mode="json"),
                "status": "needs_review",
                "updated_by": "mac",
            },
        )
        if changed:  # the user may have edited it again meanwhile; then it stays "parsing"
            rule.update(check_params=filters.model_dump(mode="json"), status="needs_review")
            report.rules_read.append(str(rule["rule_id"]))
    with store.transaction():
        store.conn.execute("DELETE FROM hidden_sessions")
        store.conn.executemany(
            "INSERT INTO hidden_sessions (session_key, hidden_at) VALUES (?, ?)",
            [(str(r["session_key"]), str(r.get("hidden_at") or "")) for r in hidden],
        )
        store.conn.executemany(
            "UPDATE standing_rules SET active = ? WHERE rule_id = ?",
            [(1 if r.get("active", True) else 0, str(r["rule_id"])) for r in managed],
        )
        store.conn.execute("DELETE FROM my_rules")
        store.conn.executemany(
            "INSERT INTO my_rules (rule_id, text, status, params, updated_at) VALUES (?, ?, ?, ?, ?)",
            [
                (
                    str(r["rule_id"]),
                    str(r["text"]),
                    str(r.get("status") or "parsing"),
                    json.dumps(r.get("check_params") or {}),
                    str(r.get("updated_at") or ""),
                )
                for r in rules
            ],
        )
    report.hidden_sessions = len(hidden)
    report.my_rules = len(rules)
    return report


def push_profile(
    store: Store, config: Config, tables: Tables, capabilities: dict[str, Any] | None = None
) -> bool:
    """Send the manager's and the user's name (and AI availability) to the app's settings.

    Only when something changed since the last push. Returns whether a row was sent.
    """
    row: dict[str, Any] = {
        "user_id": tables.user_id,
        "manager_name": config.digest.manager_name,
        "display_name": config.identity.name,
    }
    if capabilities is not None:
        row["capabilities"] = capabilities
    key = "profile_hash" if capabilities is None else "profile_caps_hash"
    digest = stable_hash(canonical_json(row))
    if store.scalar("SELECT value FROM cloud_meta WHERE key = ?", (key,)) == digest:
        return False
    tables.upsert("settings", [row], "user_id")
    store.conn.execute(
        "INSERT INTO cloud_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, digest),
    )
    return True


def post_notification(tables: Tables, kind: str, title: str, body: str) -> None:
    """Show something in the app's notifications (and its bell)."""
    tables.insert(
        "notifications", [{"user_id": tables.user_id, "kind": kind, "title": title, "body": body}]
    )
