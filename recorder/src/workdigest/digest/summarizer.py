"""Summarize each agent session of a day into a short structured record.

This runs outside the live sessions (hourly and at day close) and never prompts them.
Input per session: redacted prompts, the agent's own messages (from hooks, plus the
transcript's assistant text when the file is readable), commands, test results and files
touched. The input is capped and hashed, so an unchanged session is never re-summarized.

Everything in a summary is a *claim* (what the agent said happened). Facts come from git
and test runs, and the grounding step enforces the difference.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from workdigest.clock import now_local, parse_iso, work_date
from workdigest.config import TransmitSettings
from workdigest.llm.base import LLMError, LLMProvider, Prompt
from workdigest.models import stable_hash
from workdigest.redact.redactor import Redactor
from workdigest.store.db import Store

MAX_INPUT_CHARS = 14_000
MAX_AGENT_MESSAGES = 14
MESSAGE_CHARS = 700


class SessionSummary(BaseModel):
    """Structured summary of one session on one day."""

    model_config = ConfigDict(extra="ignore")

    headline: str = ""
    tickets: list[str] = Field(default_factory=list)
    done: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    bugs_found: list[str] = Field(default_factory=list)
    learnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)


_SYSTEM = """You summarize one AI coding-agent session for an engineer's end-of-day digest.
Use only the material given. Do not invent numbers, ticket ids, names or outcomes.
Keep the agent's exact numbers when it reports them (e.g. "650/650 tests passed").
Write short past-tense items. Return JSON only with these keys (lists of short strings):
{"headline": str, "tickets": [], "done": [], "tests": [], "decisions": [], "bugs_found": [],
 "learnings": [], "blockers": [], "risks": [], "tools_used": []}"""


def session_input(
    store: Store,
    session_key: str,
    day: str,
    *,
    redactor: Redactor,
    boundary_hour: int,
    transmit: TransmitSettings | None = None,
) -> str:
    """Build the capped, redacted text the summarizer sees for one session-day.

    ``transmit`` flags remove whole categories (prompts, agent messages, file names).
    """
    allow = transmit or TransmitSettings()
    rows = store.query(
        "SELECT kind, ts, repo, payload FROM events WHERE session_key = ? AND work_date = ? ORDER BY ts",
        (session_key, day),
    )
    session = store.query(
        "SELECT title, transcript_path, source FROM sessions WHERE session_key = ?", (session_key,)
    )
    title = session[0]["title"] if session else None
    lines: list[str] = [
        f"Session: {session_key} ({session[0]['source'] if session else '?'}) title: {title or '-'}"
    ]
    repos = sorted({r["repo"] for r in rows if r["repo"]})
    lines.append(f"Repos: {', '.join(repos) or '-'}")
    files: set[str] = set()
    for row in rows:
        payload = json.loads(row["payload"])
        kind = row["kind"]
        stamp = row["ts"][11:16]
        if kind == "prompt":
            if not allow.prompt_excerpts:
                continue
            lines.append(f"[{stamp}] USER: {payload.get('excerpt', '')}")
        elif kind in {"agent_response", "stop"}:
            text = payload.get("text") or payload.get("last_message") or ""
            if text and allow.agent_messages:
                lines.append(f"[{stamp}] AGENT: {text[:MESSAGE_CHARS]}")
        elif kind == "subagent_stop" and payload.get("summary") and allow.agent_messages:
            lines.append(
                f"[{stamp}] SUBAGENT {payload.get('agent_type') or ''}: {payload['summary'][:MESSAGE_CHARS]}"
            )
        elif kind in {"tool_use", "shell"} and payload.get("command"):
            result = payload.get("test_result")
            suffix = f"  => {result['passed']}/{result['total']} passed" if result else ""
            lines.append(f"[{stamp}] RAN: {payload['command'][:200]}{suffix}")
        if payload.get("file_path") and (payload.get("is_edit") or kind == "file_edit"):
            files.add(payload["file_path"])
    if files and allow.file_paths:
        lines.append("Files edited: " + ", ".join(sorted(files)[:40]))
    path = session[0]["transcript_path"] if session else None
    if allow.agent_messages:
        lines += _transcript_agent_text(path, day, boundary_hour, redactor)
    text = "\n".join(lines)
    return (
        text
        if len(text) <= MAX_INPUT_CHARS
        else text[:3000] + "\n…\n" + text[-(MAX_INPUT_CHARS - 3000) :]
    )


def _transcript_agent_text(
    path: str | None, day: str, boundary_hour: int, redactor: Redactor
) -> list[str]:
    """Last assistant messages of the day from a Claude Code transcript (Cursor lines lack timestamps)."""
    if not path or not Path(path).is_file():
        return []
    messages: list[str] = []
    try:
        with Path(path).open(encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                if '"assistant"' not in raw:
                    continue
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                ts = parse_iso(str(entry.get("timestamp") or ""))
                if ts is None or work_date(ts, boundary_hour).isoformat() != day:
                    continue
                content = (entry.get("message") or {}).get("content")
                for block in content if isinstance(content, list) else []:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "text"
                        and len(str(block.get("text"))) > 40
                    ):
                        messages.append(str(block["text"]))
    except OSError:
        return []
    picked = messages[-MAX_AGENT_MESSAGES:]
    return [f"AGENT (transcript): {redactor.redact_text(m[:MESSAGE_CHARS]).value}" for m in picked]


def deterministic_summary(store: Store, session_key: str, day: str) -> SessionSummary:
    """No-LLM fallback: title, tickets and test results only (no narrative is invented)."""
    session = store.query("SELECT title FROM sessions WHERE session_key = ?", (session_key,))
    tickets: dict[str, None] = {}
    for row in store.query(
        "SELECT ticket_keys FROM events WHERE session_key = ? AND work_date = ?", (session_key, day)
    ):
        tickets.update(dict.fromkeys(json.loads(row["ticket_keys"])))
    tests = [
        f"{r['repo'] or '?'} {r['runner']}: {r['passed']}/{r['total']}"
        for r in store.query(
            "SELECT repo, runner, passed, total FROM test_runs WHERE session_key = ? AND work_date = ? ORDER BY ts",
            (session_key, day),
        )
    ]
    title = session[0]["title"] if session else None
    return SessionSummary(headline=title or "", tickets=list(tickets), tests=tests[-6:])


def summarize_day(
    store: Store,
    day: str,
    *,
    redactor: Redactor,
    boundary_hour: int,
    llm: LLMProvider | None,
    transmit: TransmitSettings | None = None,
    concurrency: int = 4,
) -> dict[str, SessionSummary]:
    """Summarize every session active on ``day`` (cached by input hash, LLM calls in parallel)."""
    keys = [
        r["session_key"]
        for r in store.query(
            "SELECT DISTINCT session_key FROM events WHERE work_date = ? AND session_key IS NOT NULL "
            "AND session_key NOT IN (SELECT session_key FROM hidden_sessions) ORDER BY session_key",
            (day,),
        )
    ]
    out: dict[str, SessionSummary] = {}
    pending: list[tuple[str, str, str]] = []  # (session key, input text, input hash)
    method = llm.name if llm else "deterministic"
    for key in keys:
        text = session_input(
            store, key, day, redactor=redactor, boundary_hour=boundary_hour, transmit=transmit
        )
        digest = stable_hash(method, text)
        cached = store.query(
            "SELECT summary FROM session_summaries WHERE session_key = ? AND work_date = ? AND input_hash = ?",
            (key, day, digest),
        )
        if cached:
            out[key] = SessionSummary.model_validate(json.loads(cached[0]["summary"]))
        else:
            pending.append((key, text, digest))

    # Only the LLM calls run in parallel; every SQLite read and write stays on this thread.
    results: dict[str, SessionSummary | LLMError] = {}
    if llm is not None and pending:
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            futures = {key: pool.submit(_summarize_one, llm, text) for key, text, _ in pending}
            results = {key: future.result() for key, future in futures.items()}
    for key, _, digest in pending:
        result = results.get(key)
        used = method
        if isinstance(result, SessionSummary):
            summary = result
        else:
            if isinstance(result, LLMError):
                store.log_error("summarize", f"{key}: {result}")
            summary = deterministic_summary(store, key, day)
            used = "deterministic"
        store.conn.execute(
            "INSERT OR REPLACE INTO session_summaries VALUES (?, ?, ?, ?, ?, ?)",
            (key, day, digest, used, summary.model_dump_json(), now_local().isoformat()),
        )
        out[key] = summary
    return out


def _summarize_one(llm: LLMProvider, text: str) -> SessionSummary | LLMError:
    try:
        return llm.complete_json(Prompt(system=_SYSTEM, user=text), SessionSummary)
    except LLMError as exc:
        return exc


def summary_line(summary: SessionSummary) -> str:
    """Compact one-paragraph rendering used inside the evidence pack."""
    parts: list[str] = []
    for label, values in (
        ("done", summary.done),
        ("tests", summary.tests),
        ("decisions", summary.decisions),
        ("bugs found", summary.bugs_found),
        ("learnings", summary.learnings),
        ("blockers", summary.blockers),
        ("risks", summary.risks),
        ("tools", summary.tools_used),
    ):
        if values:
            parts.append(f"{label}: " + "; ".join(values))
    return " | ".join(parts)
