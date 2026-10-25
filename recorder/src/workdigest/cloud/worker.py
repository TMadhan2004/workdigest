"""The Mac-side worker: heartbeat, pull, run queued jobs, push.

The app (and the 20:00 schedule) request work by inserting ``generation_jobs`` rows. Only
the Mac can do that work, because it holds the evidence, the logged-in Cursor CLI and the
Atlassian tokens. A job is claimed atomically: one UPDATE from ``queued`` to ``running``,
so two workers never run the same job.
"""

from __future__ import annotations

import platform
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from workdigest.clock import now_local, work_date
from workdigest.cloud.sync import (
    PullReport,
    Tables,
    UserStateReport,
    ensure_template,
    pull,
    push,
    push_profile,
    sync_user_state,
)
from workdigest.digest.draft import Draft
from workdigest.digest.my_rules import RuleFilters, interpret, rule_context
from workdigest.digest.service import (
    DigestContext,
    add_reply,
    close_day,
    create_draft,
    make_provider,
    regenerate,
    sync_atlassian,
)
from workdigest.digest.template import load_template
from workdigest.llm.base import LLMProvider
from workdigest.pipeline import run_pass
from workdigest.version import __version__

VERSION = __version__
MAX_JOBS_PER_TICK = 5


@dataclass(slots=True)
class TickReport:
    """One worker iteration."""

    pulled: PullReport | None = None
    user_state: UserStateReport | None = None
    jobs_done: list[str] = field(default_factory=list)
    jobs_failed: list[str] = field(default_factory=list)
    pushed: dict[str, int] = field(default_factory=dict)
    # (title, body) of evening-nudge notifications posted this tick, for a desktop alert.
    nudges: list[tuple[str, str]] = field(default_factory=list)
    # A draft was written without the chosen AI (it failed), so the provider needs checking.
    ai_fell_back: bool = False


ProviderFactory = Callable[[DigestContext], LLMProvider | None]


def _default_provider(ctx: DigestContext) -> LLMProvider | None:
    return make_provider(ctx.config)


DeviceStatus = Literal["idle", "drafting", "syncing", "offline"]
DRAFTING_KINDS = {"full", "section"}


def heartbeat(
    tables: Tables, device_id: str, status: DeviceStatus = "idle", current_job: str | None = None
) -> None:
    """Tell the app this Mac is online and what it is doing (stale heartbeat = "Mac offline")."""
    tables.upsert(
        "devices",
        [
            {
                "user_id": tables.user_id,
                "device_id": device_id,
                "kind": "mac",
                "label": platform.node(),
                "app_version": VERSION,
                "last_seen_at": now_local().isoformat(),
                "status": status,
                "current_job": current_job,
            }
        ],
        "user_id,device_id",
    )


class Presence:
    """This Mac's live status, shared by the job runner and the heartbeat thread.

    The runner sets "drafting"/"syncing" while a job runs; the heartbeat thread re-sends the
    current status every few seconds, so a long job never looks like "offline".
    """

    def __init__(self, tables: Tables, device_id: str) -> None:
        """Bind to one device row."""
        self.tables = tables
        self.device_id = device_id
        self._lock = threading.Lock()
        self.status: DeviceStatus = "idle"
        self.current_job: str | None = None

    def set(self, status: DeviceStatus, current_job: str | None = None) -> None:
        """Change the status and publish it immediately."""
        with self._lock:
            self.status, self.current_job = status, current_job
        self.beat()

    def beat(self) -> None:
        """Publish the current status (and refresh last_seen_at)."""
        with self._lock:
            status, job = self.status, self.current_job
        heartbeat(self.tables, self.device_id, status, job)


def tick(
    ctx: DigestContext,
    tables: Tables,
    device_id: str,
    *,
    provider_factory: ProviderFactory = _default_provider,
    refresh: bool = True,
    presence: Presence | None = None,
) -> TickReport:
    """Heartbeat, pull app changes, run queued jobs, push results."""
    report = TickReport()
    presence = presence or Presence(tables, device_id)
    presence.beat()

    def on_new_reply(reply_id: str, text: str, for_date: str | None) -> None:
        add_reply(
            ctx.store,
            ctx.config,
            text,
            date.fromisoformat(for_date) if for_date else None,
            provider_factory(ctx),
            confirm=False,
            reply_id=reply_id,
        )

    report.pulled = pull(ctx.store, ctx.config, tables, on_new_reply, overrides_dir=ctx.paths.home)

    def read_rule(text: str) -> RuleFilters:
        repos, prs = rule_context(ctx.store, (r.display_name for r in ctx.config.repos))
        return interpret(text, repos, prs, provider_factory(ctx))

    report.user_state = sync_user_state(ctx.store, tables, read_rule)
    jobs = tables.select(
        "generation_jobs",
        {"status": "eq.queued", "order": "created_at.asc", "limit": str(MAX_JOBS_PER_TICK)},
    )
    for job in jobs:
        claimed = tables.update(
            "generation_jobs",
            {"id": f"eq.{job['id']}", "status": "eq.queued"},
            {"status": "running", "claimed_by": device_id, "started_at": now_local().isoformat()},
        )
        if not claimed:
            continue  # another worker took it
        presence.set("drafting" if job["kind"] in DRAFTING_KINDS else "syncing", str(job["id"]))
        try:
            result = _run_job(ctx, tables, job, provider_factory, refresh=refresh)
            tables.update(
                "generation_jobs",
                {"id": f"eq.{job['id']}"},
                {"status": "done", "result": result, "finished_at": now_local().isoformat()},
            )
            report.jobs_done.append(str(job["id"]))
            report.ai_fell_back = report.ai_fell_back or bool(result.get("ai_fell_back"))
            if note := result.get("nudge"):
                report.nudges.append((str(note[0]), str(note[1])))
        except Exception as exc:  # noqa: BLE001 - a failed job is reported, never crashes the worker
            ctx.store.log_error("cloud.job", traceback.format_exc(), str(job["id"]))
            tables.update(
                "generation_jobs",
                {"id": f"eq.{job['id']}"},
                {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                    "finished_at": now_local().isoformat(),
                },
            )
            report.jobs_failed.append(str(job["id"]))
        finally:
            presence.set("idle")
    report.pushed = push(ctx.store, ctx.config, tables)
    if push_profile(ctx.store, ctx.config, tables):
        report.pushed["settings"] = 1
    template = load_template(ctx.paths.home / "template.toml")
    if ensure_template(tables, template.model_dump(mode="json"), template.version):
        report.pushed["templates"] = 1
    return report


def _job_day(ctx: DigestContext, params: dict[str, Any]) -> date:
    value = params.get("work_date")
    return (
        date.fromisoformat(str(value))
        if value
        else work_date(now_local(), ctx.config.day.boundary_hour)
    )


def _run_job(
    ctx: DigestContext,
    tables: Tables,
    job: dict[str, Any],
    provider_factory: ProviderFactory,
    *,
    refresh: bool,
) -> dict[str, Any]:
    params = job.get("params") or {}
    kind = job["kind"]
    day = _job_day(ctx, params)
    if kind in {"full", "sync"} and refresh:
        run_pass(ctx.store, ctx.config, ctx.paths)
        sync_atlassian(ctx, day)
    if kind == "sync":
        return {"work_date": day.isoformat()}
    if kind == "full":
        if any(params.get(k) for k in ("rating", "team_members", "project")):
            close_day(
                ctx.store,
                day,
                rating=params.get("rating"),
                project=params.get("project"),
                team=params.get("team_members"),
            )
        digest_id, draft = create_draft(ctx, day, llm=provider_factory(ctx))
        nudge = bool(params.get("nudge"))
        note = _notify_draft(
            tables, digest_id, draft, nudge=nudge, send_by=ctx.config.digest.send_deadline
        )
        wanted_ai = ctx.config.digest.provider != "none" and ctx.config.transmit.llm_enabled
        fell_back = wanted_ai and draft.provider == "none"
        return {
            **_summary(digest_id, draft),
            **({"nudge": list(note)} if nudge else {}),
            **({"ai_fell_back": True} if fell_back else {}),
        }
    if kind == "section":
        llm = provider_factory(ctx)
        if llm is None:
            raise ValueError("regenerating a section needs an LLM provider")
        digest_id, draft = regenerate(ctx, day, str(params["section"]), llm=llm)
        return _summary(digest_id, draft)
    if kind == "parse_reply":
        text = str(params.get("text") or "")
        if not text:
            raise ValueError("parse_reply needs params.text")
        reply_id, parsed, _ = add_reply(
            ctx.store, ctx.config, text, None, provider_factory(ctx), confirm=False
        )
        return {"reply_id": reply_id, "items": len(parsed.items)}
    raise ValueError(f"unknown job kind {kind!r}")


def _summary(digest_id: str, draft: Draft) -> dict[str, Any]:
    return {
        "digest_id": digest_id,
        "work_date": draft.work_date.isoformat(),
        "gaps": len(draft.gaps),
        "provider": draft.provider,
    }


def _notify_draft(
    tables: Tables, digest_id: str, draft: Draft, *, nudge: bool, send_by: str = ""
) -> tuple[str, str]:
    to_confirm = sum(1 for g in draft.gaps if g.kind != "input")
    title = f"Draft ready – send by {send_by}" if nudge and send_by else "Draft ready"
    body = f"{draft.work_date:%a %d %b}: {to_confirm} gaps to confirm"
    tables.insert(
        "notifications",
        [
            {
                "user_id": tables.user_id,
                "kind": "draft_ready",
                "title": title,
                "body": body,
                "payload": {"digest_id": digest_id, "work_date": draft.work_date.isoformat()},
            }
        ],
    )
    return title, body
