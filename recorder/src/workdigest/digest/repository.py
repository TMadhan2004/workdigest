"""Persistence for the digest engine: digests, replies, asks, standing rules and plans."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from workdigest.clock import now_local
from workdigest.digest.document import ParsedDigest, parse_digest
from workdigest.digest.reply_parser import ParsedReply, ReplyItem
from workdigest.digest.rules import SEED_RULES, StandingRule
from workdigest.models import canonical_json, stable_hash
from workdigest.store.db import Store

ASK_STATUSES = ("open", "done", "disagree", "waiting", "cleared")
_WORD = re.compile(r"[a-z0-9]+(?:-[0-9]+)?")
_URL = re.compile(r"https?://\S+")
# Verbs and filler the manager uses in every ask; they say nothing about *which* ask it is.
_GENERIC = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "to",
    "of",
    "on",
    "in",
    "for",
    "with",
    "is",
    "are",
    "it",
    "please",
    "your",
    "you",
    "my",
    "i",
    "as",
    "at",
    "be",
    "this",
    "that",
    "each",
    "both",
    "also",
    "raise",
    "add",
    "log",
    "run",
    "use",
    "move",
    "open",
    "share",
    "check",
    "so",
    "can",
    "we",
    "see",
    "from",
    "when",
    "them",
    "all",
    "which",
    "one",
    "will",
    "would",
    "have",
    "has",
    "not",
    "yet",
    "still",
    "there",
    "its",
    "any",
    "into",
    "then",
    "they",
    "him",
    "his",
    "me",
    "today",
    "tomorrow",
    "digest",
    "work",
    "short",
    "right",
    "status",
    "rest",
    "issues",
    "about",
    "what",
    "how",
    "where",
    "who",
    "does",
    "did",
    "do",
    "more",
    "just",
    "only",
    "been",
    "was",
    "were",
    "than",
    "could",
    "should",
    "like",
    "need",
    "needs",
    "make",
    "made",
    "even",
    "while",
    "once",
    "before",
    "after",
    "their",
    "our",
    "other",
    "between",
    "these",
    "those",
    "some",
    "such",
    "same",
    "whenever",
}
SIMILAR_ASK_THRESHOLD = 0.4
SAME_TICKET_THRESHOLD = 0.25
MIN_SHARED_WORDS = 2


def _tokens(text: str) -> set[str]:
    return {
        w for w in _WORD.findall(_URL.sub(" ", text.lower())) if w not in _GENERIC and len(w) > 2
    }


def similarity(a: str, b: str) -> tuple[float, int]:
    """Overlap of distinctive words: (shared / size of the smaller set, number shared).

    Repeated asks are reworded and grow longer each day ("Raise a bug with steps…" → "For
    tomorrow, raise the OTP bug…"), so Jaccard underrates them. The overlap coefficient on
    distinctive words matches how the manager repeats himself. Calibrated on the 19–23 Oct replies.
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0, 0
    shared = len(ta & tb)
    return shared / min(len(ta), len(tb)), shared


def is_same_ask(a: str, b: str, *, same_ticket: bool = False) -> bool:
    """True when ``b`` restates ``a``."""
    score, shared = similarity(a, b)
    if shared < MIN_SHARED_WORDS:
        return False
    return score >= (SAME_TICKET_THRESHOLD if same_ticket else SIMILAR_ASK_THRESHOLD)


@dataclass(frozen=True, slots=True)
class Ask:
    """An open (or closed) request from the manager."""

    ask_id: str
    text: str
    kind: str
    related_tickets: list[str]
    status: str
    needs_real_action: bool
    evidence: str | None
    reason: str | None
    opened_on: str
    closed_on: str | None
    times_raised: int


class DigestRepository:
    """Digest-domain reads and writes on the shared SQLite store."""

    def __init__(self, store: Store) -> None:
        """Wrap ``store``."""
        self.store = store

    # ---------------------------------------------------------------- digests
    def save_digest(
        self,
        work_date: date,
        text: str,
        *,
        origin: str,
        status: str = "final",
        template_version: str | None = None,
        structure: dict[str, Any] | None = None,
        gaps: list[dict[str, Any]] | None = None,
    ) -> str:
        """Store a digest version and return its id (same text on the same day is a no-op)."""
        digest_id = stable_hash(work_date.isoformat(), origin, text)[:24]
        self.store.conn.execute(
            """
            INSERT OR IGNORE INTO digests
                (digest_id, work_date, origin, status, template_version, text, structure, gaps, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                digest_id,
                work_date.isoformat(),
                origin,
                status,
                template_version,
                text,
                canonical_json(structure or {}),
                canonical_json(gaps or []),
                now_local().isoformat(),
            ),
        )
        return digest_id

    def save_statements(self, digest_id: str, rows: list[dict[str, Any]]) -> None:
        """Replace the statements (evidence links) of a generated digest."""
        self.store.conn.execute("DELETE FROM digest_statements WHERE digest_id = ?", (digest_id,))
        self.store.conn.executemany(
            """
            INSERT INTO digest_statements (digest_id, section_key, idx, text, verified, evidence_ids, problems)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    digest_id,
                    r["section_key"],
                    r["idx"],
                    r["text"],
                    int(r["verified"]),
                    canonical_json(r["evidence_ids"]),
                    canonical_json(r["problems"]),
                )
                for r in rows
            ],
        )

    def latest_digest_before(
        self, work_date: date, *, final_only: bool = True
    ) -> tuple[str, ParsedDigest] | None:
        """Most recent digest strictly before ``work_date`` (sent ones by default)."""
        statuses = ("final", "sent_marked") if final_only else ("draft", "final", "sent_marked")
        marks = ",".join("?" * len(statuses))
        row = self.store.conn.execute(
            f"SELECT work_date, text FROM digests WHERE work_date < ? AND status IN ({marks}) "  # noqa: S608 - only "?" placeholders are interpolated
            "ORDER BY work_date DESC, created_at DESC LIMIT 1",
            (work_date.isoformat(), *statuses),
        ).fetchone()
        return (row["work_date"], parse_digest(row["text"])) if row else None

    def known_ticket_titles(self) -> dict[str, str]:
        """Ticket titles: Jira (when synced) first, else the Jira lists of digests actually sent.

        Generated drafts are never a source, since their titles may be placeholders.
        """
        titles: dict[str, str] = {}
        for row in self.store.query(
            "SELECT text FROM digests WHERE origin = 'imported' OR status IN ('final', 'sent_marked') "
            "ORDER BY work_date, created_at"
        ):
            for entry in parse_digest(row["text"]).jira:
                if not entry.title.startswith("["):
                    titles[entry.key] = entry.title
        for row in self.store.query("SELECT key, title FROM tickets WHERE title != ''"):
            titles[row["key"]] = row["title"]
        return titles

    # ---------------------------------------------------------------- replies
    def save_reply(
        self,
        raw_text: str,
        for_work_date: date | None,
        parsed: ParsedReply,
        *,
        reply_id: str | None = None,
    ) -> str:
        """Store a reply and its proposed items. Re-saving the same text keeps item statuses."""
        normalized = " ".join(raw_text.split())
        reply_id = (
            reply_id or stable_hash(normalized)[:24]
        )  # the app's id is kept for app-side replies
        exists = self.store.scalar("SELECT 1 FROM manager_replies WHERE reply_id = ?", (reply_id,))
        if exists:
            return reply_id
        self.store.conn.execute(
            "INSERT INTO manager_replies (reply_id, for_work_date, raw_text, received_at) VALUES (?, ?, ?, ?)",
            (
                reply_id,
                for_work_date.isoformat() if for_work_date else None,
                raw_text,
                now_local().isoformat(),
            ),
        )
        for item in parsed.items:
            self.store.conn.execute(
                """
                INSERT INTO reply_items
                    (item_id, reply_id, idx, type, heading, text, related_tickets, due_date, status, method)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?)
                """,
                (
                    f"{reply_id}:{item.idx}",
                    reply_id,
                    item.idx,
                    item.type,
                    item.heading,
                    item.text,
                    canonical_json(item.related_tickets),
                    item.due_date,
                    item.method,
                ),
            )
        return reply_id

    def reply_items(self, reply_id: str) -> list[dict[str, Any]]:
        """Items of one reply in order."""
        return [
            {**dict(r), "related_tickets": json.loads(r["related_tickets"])}
            for r in self.store.query(
                "SELECT * FROM reply_items WHERE reply_id = ? ORDER BY idx", (reply_id,)
            )
        ]

    def set_item(
        self,
        item_id: str,
        *,
        type_: str | None = None,
        status: str | None = None,
        text: str | None = None,
    ) -> None:
        """Edit a proposed item (the user's review step)."""
        if type_:
            self.store.conn.execute(
                "UPDATE reply_items SET type = ?, method = 'manual' WHERE item_id = ?",
                (type_, item_id),
            )
        if text:
            self.store.conn.execute(
                "UPDATE reply_items SET text = ?, method = 'manual' WHERE item_id = ?",
                (text, item_id),
            )
        if status:
            self.store.conn.execute(
                "UPDATE reply_items SET status = ? WHERE item_id = ?", (status, item_id)
            )

    def confirm_reply(
        self, reply_id: str, opened_on: date, items: list[ReplyItem] | None = None
    ) -> dict[str, int]:
        """Promote a reply's items: actions/questions become asks, standing rules become rules.

        Repeated asks (same ticket, or similar wording) are merged into the existing open ask,
        and its ``times_raised`` counter goes up instead of a duplicate being created.
        """
        counts = {"asks_new": 0, "asks_repeated": 0, "rules_new": 0, "skipped": 0}
        real_action = {i.idx: i.needs_real_action for i in items or []}
        for row in self.reply_items(reply_id):
            if row["status"] == "dismissed":
                counts["skipped"] += 1
                continue
            self.store.conn.execute(
                "UPDATE reply_items SET status = 'confirmed' WHERE item_id = ?", (row["item_id"],)
            )
            if row["type"] in {"one_time_action", "question"}:
                merged = self._merge_into_open_ask(row)
                if merged:
                    counts["asks_repeated"] += 1
                    continue
                self.store.conn.execute(
                    """
                    INSERT OR IGNORE INTO asks
                        (ask_id, source_item_id, text, kind, related_tickets, status, needs_real_action, opened_on)
                    VALUES (?, ?, ?, ?, ?, 'open', ?, ?)
                    """,
                    (
                        stable_hash(row["item_id"])[:16],
                        row["item_id"],
                        row["text"],
                        row["type"],
                        canonical_json(row["related_tickets"]),
                        int(real_action.get(row["idx"], False)),
                        opened_on.isoformat(),
                    ),
                )
                counts["asks_new"] += 1
            elif row["type"] == "standing_rule":
                if self._rule_exists_like(row["text"]):
                    counts["skipped"] += 1
                    continue
                self.store.conn.execute(
                    """
                    INSERT OR IGNORE INTO standing_rules (rule_id, text, check_kind, check_params, source, active, created_at)
                    VALUES (?, ?, 'manual', '{}', ?, 1, ?)
                    """,
                    (
                        f"U-{stable_hash(row['item_id'])[:8]}",
                        row["text"],
                        row["item_id"],
                        now_local().isoformat(),
                    ),
                )
                counts["rules_new"] += 1
        return counts

    def _merge_into_open_ask(self, row: dict[str, Any]) -> bool:
        tickets = set(row["related_tickets"])
        for ask in self.asks(status="open"):
            if ask.kind != row["type"]:
                continue  # a question must stay separate so it gets answered
            same_ticket = bool(tickets and tickets & set(ask.related_tickets))
            if is_same_ask(ask.text, row["text"], same_ticket=same_ticket):
                self.store.conn.execute(
                    "UPDATE asks SET times_raised = times_raised + 1 WHERE ask_id = ?",
                    (ask.ask_id,),
                )
                return True
        return False

    def _rule_exists_like(self, text: str) -> bool:
        return any(is_same_ask(rule.text, text) for rule in self.rules())

    # ------------------------------------------------------------------ asks
    def asks(self, status: str | None = None) -> list[Ask]:
        """All asks, or those with ``status``, oldest first."""
        sql = "SELECT * FROM asks"
        params: tuple[Any, ...] = ()
        if status:
            sql += " WHERE status = ?"
            params = (status,)
        rows = self.store.query(sql + " ORDER BY opened_on, ask_id", params)
        return [
            Ask(
                ask_id=r["ask_id"],
                text=r["text"],
                kind=r["kind"],
                related_tickets=json.loads(r["related_tickets"]),
                status=r["status"],
                needs_real_action=bool(r["needs_real_action"]),
                evidence=r["evidence"],
                reason=r["reason"],
                opened_on=r["opened_on"],
                closed_on=r["closed_on"],
                times_raised=r["times_raised"],
            )
            for r in rows
        ]

    def closed_asks(self, after: str | None, until: str) -> list[Ask]:
        """Asks answered Done or Disagree after ``after`` (exclusive) up to ``until`` (inclusive).

        These are what the next digest's closing reports; each one is reported once, in the first
        digest after it was closed. Without a previous digest only ``until``'s day counts.
        """
        start = after or until
        op = ">" if after else ">="
        return [
            ask
            for ask in self.asks()
            if ask.status in {"done", "disagree"}
            and ask.closed_on is not None
            and (ask.closed_on > start if op == ">" else ask.closed_on >= start)
            and ask.closed_on <= until
        ]

    def set_ask_status(
        self, ask_id: str, status: str, *, evidence: str | None = None, reason: str | None = None
    ) -> None:
        """Move an ask to ``status``. ``done`` needs evidence; ``disagree`` needs a reason."""
        if status not in ASK_STATUSES:
            raise ValueError(f"status must be one of {ASK_STATUSES}")
        if status == "done" and not evidence:
            raise ValueError("closing an ask as done needs evidence (a link, ticket or commit)")
        if status == "disagree" and not reason:
            raise ValueError("disagreeing needs a reason")
        matches = self.store.query("SELECT ask_id FROM asks WHERE ask_id LIKE ?", (f"{ask_id}%",))
        if len(matches) != 1:
            raise ValueError(f"ask id {ask_id!r} matches {len(matches)} asks")
        closed = (
            now_local().date().isoformat() if status in {"done", "disagree", "cleared"} else None
        )
        self.store.conn.execute(
            "UPDATE asks SET status = ?, evidence = COALESCE(?, evidence), reason = COALESCE(?, reason), closed_on = ? "
            "WHERE ask_id = ?",
            (status, evidence, reason, closed, matches[0]["ask_id"]),
        )

    # ----------------------------------------------------------------- rules
    def seed_rules(self) -> int:
        """Insert the built-in standing rules (idempotent; skipped once the engineer has their own)."""
        own = self.store.scalar("SELECT value FROM cloud_meta WHERE key = 'rule_set'") == "own"
        return 0 if own else self._insert_seed(SEED_RULES)

    def use_own_rules(self, rules: list[StandingRule]) -> int:
        """Replace the built-in checks with ones learned from the engineer's own format."""
        self.store.conn.execute("DELETE FROM standing_rules WHERE source = 'seed'")
        self.store.conn.execute(
            "INSERT INTO cloud_meta (key, value) VALUES ('rule_set', 'own') "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        )
        return self._insert_seed(rules)

    def _insert_seed(self, rules: tuple[StandingRule, ...] | list[StandingRule]) -> int:
        inserted = 0
        for rule in rules:
            cursor = self.store.conn.execute(
                """
                INSERT OR IGNORE INTO standing_rules (rule_id, text, check_kind, check_params, source, active, created_at)
                VALUES (?, ?, ?, ?, 'seed', 1, ?)
                """,
                (
                    rule.rule_id,
                    rule.text,
                    rule.check_kind,
                    canonical_json(rule.check_params),
                    now_local().isoformat(),
                ),
            )
            inserted += cursor.rowcount
        return inserted

    def rules(self, active_only: bool = True) -> list[StandingRule]:
        """Standing rules, seed rules first."""
        sql = (
            "SELECT * FROM standing_rules WHERE active = 1 ORDER BY source != 'seed', rule_id"
            if active_only
            else "SELECT * FROM standing_rules ORDER BY source != 'seed', rule_id"
        )
        return [
            StandingRule(
                rule_id=r["rule_id"],
                text=r["text"],
                check_kind=r["check_kind"],
                check_params=json.loads(r["check_params"]),
                source=r["source"],
                active=bool(r["active"]),
            )
            for r in self.store.query(sql)
        ]

    # ----------------------------------------------------------------- plans
    def save_plan(self, plan_date: date, items: list[str], horizon: int = 5) -> None:
        """Record the plan set by the digest of ``plan_date``."""
        self.store.conn.execute(
            "INSERT OR REPLACE INTO plans (plan_date, horizon, items) VALUES (?, ?, ?)",
            (plan_date.isoformat(), horizon, canonical_json(items)),
        )

    def plans(self) -> list[tuple[date, int, list[str]]]:
        """All plans, oldest first."""
        return [
            (date.fromisoformat(r["plan_date"]), r["horizon"], json.loads(r["items"]))
            for r in self.store.query("SELECT * FROM plans ORDER BY plan_date")
        ]
