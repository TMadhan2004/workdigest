"""Turn raw hook payloads (Claude Code or Cursor) into redacted :class:`Event` objects.

Claude Code and Cursor send different payload shapes, and Cursor can also run Claude
Code hooks ("Third-Party Imports", on by default). The source is therefore detected from
the payload itself, not from the hook that was configured. Every capture path derives the
same ``event_id`` for the same fact (a tool call keeps its ``tool_use_id`` and a prompt its
normalized text hash), so duplicates collapse on insert.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from workdigest.clock import local_tz, parse_iso, work_date
from workdigest.config import Config
from workdigest.ingest.context import RepoResolver, TicketMatcher
from workdigest.ingest.test_parsers import parse_test_output
from workdigest.models import Event, Kind, Origin, Source, canonical_json, stable_hash
from workdigest.redact.redactor import Redactor

_CLAUDE_KINDS: dict[str, Kind] = {
    "sessionstart": Kind.SESSION_START,
    "userpromptsubmit": Kind.PROMPT,
    "posttooluse": Kind.TOOL_USE,
    "stop": Kind.STOP,
    "subagentstop": Kind.SUBAGENT_STOP,
    "sessionend": Kind.SESSION_END,
}
_CURSOR_KINDS: dict[str, Kind] = {
    "sessionstart": Kind.SESSION_START,
    "beforesubmitprompt": Kind.PROMPT,
    "userpromptsubmit": Kind.PROMPT,
    "posttooluse": Kind.TOOL_USE,
    "aftershellexecution": Kind.SHELL,
    "afterfileedit": Kind.FILE_EDIT,
    "afteragentresponse": Kind.AGENT_RESPONSE,
    "stop": Kind.STOP,
    "subagentstop": Kind.SUBAGENT_STOP,
    "sessionend": Kind.SESSION_END,
}
_SHELL_TOOLS = {"bash", "shell", "run_terminal_cmd", "terminal", "run_command"}
_EDIT_TOOLS = {
    "edit",
    "write",
    "multiedit",
    "notebookedit",
    "edit_file",
    "write_file",
    "search_replace",
    "strreplace",
}
_PATH_KEYS = ("file_path", "path", "notebook_path", "target_file")
_REDACTION_MARKER = re.compile(r"\[REDACTED:([a-z0-9_]+)\]")
_CD_PREFIX = re.compile(r"^\s*cd\s+(?P<q>['\"]?)(?P<dir>[^'\"&;|]+?)(?P=q)\s*(?:&&|;|$)")


def effective_dir(cwd: str | None, payload: dict[str, Any]) -> str | None:
    """Best directory for repo attribution.

    Agents often run from a parent folder that holds many repos, so the edited file, or
    a leading ``cd <dir> &&`` in a command, says more than the session cwd.
    """
    path = payload.get("file_path")
    if isinstance(path, str) and path.startswith(("/", "~")):
        return str(Path(path).expanduser().parent)
    command = payload.get("command")
    if isinstance(command, str):
        match = _CD_PREFIX.match(command)
        if match:
            target = Path(match["dir"].strip()).expanduser()
            if not target.is_absolute() and cwd:
                target = Path(cwd) / target
            return str(target)
    return cwd


class UnsupportedPayloadError(ValueError):
    """The payload is not a hook event this recorder understands."""


class IgnoredEventError(UnsupportedPayloadError):
    """An expected skip, not a fault: dropped without an ingest_errors row."""


class OwnSandboxEventError(IgnoredEventError):
    """The event came from WorkDigest's own LLM call."""


class ExcludedPathEventError(IgnoredEventError):
    """The event's folder is under ``capture.exclude_paths``."""


OWN_SANDBOX_MARKER = "workdigest-llm-"


def is_own_sandbox(path: object) -> bool:
    """True for WorkDigest's LLM sandbox, whose agent activity must never count as work."""
    return isinstance(path, str) and OWN_SANDBOX_MARKER in path


def detect_source(payload: dict[str, Any], hint: str) -> Source:
    """Decide which agent produced ``payload``.

    Cursor payloads always carry ``cursor_version`` or ``conversation_id``. This also
    applies when Cursor runs hooks configured for Claude Code.
    """
    if "cursor_version" in payload or "conversation_id" in payload:
        return Source.CURSOR
    if "session_id" in payload and "hook_event_name" in payload:
        return Source.CLAUDE_CODE
    if hint == Source.CURSOR.value:
        return Source.CURSOR
    if hint == Source.CLAUDE_CODE.value:
        return Source.CLAUDE_CODE
    raise UnsupportedPayloadError("payload has neither Claude Code nor Cursor fields")


def normalize_prompt(text: str) -> str:
    """Canonical prompt text used for cross-path dedupe."""
    return " ".join(text.split())


@dataclass(slots=True)
class _Draft:
    kind: Kind
    natural_key: str
    payload: dict[str, Any]
    ticket_texts: tuple[str | None, ...] = ()


class EventFactory:
    """Builds redacted, enriched events. Shared by hooks, transcripts and pollers."""

    def __init__(
        self,
        config: Config,
        redactor: Redactor,
        tickets: TicketMatcher,
        resolver: RepoResolver,
    ) -> None:
        """Create a factory bound to one configuration."""
        self.config = config
        self.redactor = redactor
        self.tickets = tickets
        self.resolver = resolver

    # ------------------------------------------------------------- generic
    def build(
        self,
        *,
        source: Source,
        origin: Origin,
        kind: Kind,
        natural_key: str,
        ts: datetime,
        payload: dict[str, Any],
        session_key: str | None = None,
        cwd: str | None = None,
        repo: str | None = None,
        branch: str | None = None,
        ticket_texts: tuple[str | None, ...] = (),
        ts_approx: bool = False,
    ) -> Event:
        """Redact ``payload``, attach repo, branch and ticket context, and return the event."""
        redaction = self.redactor.redact(payload)
        lookup = effective_dir(cwd, payload)
        if repo is None:
            repo = self._configured_repo(lookup)
        if repo is None:
            context = self.resolver.resolve(lookup)
            if context.name is not None and lookup != cwd:
                branch = context.branch  # the touched repo's branch, not the session's
            elif context.name is None and lookup != cwd:
                context = self.resolver.resolve(cwd)
            repo = context.name
            branch = branch or context.branch
        redacted_payload: dict[str, Any] = redaction.value
        # Builders redact before truncating; recover those rule names from the markers.
        rules = redaction.rules_hit | set(
            _REDACTION_MARKER.findall(canonical_json(redacted_payload))
        )
        tickets = self.tickets.find(*ticket_texts, branch)
        return Event(
            event_id=Event.make_id(source, session_key, kind, natural_key),
            source=source,
            origin=origin,
            kind=kind,
            ts=ts.astimezone(local_tz()),
            ts_approx=ts_approx,
            work_date=work_date(ts, self.config.day.boundary_hour).isoformat(),
            session_key=session_key,
            cwd=cwd,
            repo=repo,
            branch=branch,
            ticket_keys=tickets,
            payload=redacted_payload,
            redaction_rules=sorted(rules),
        )

    def is_excluded(self, directory: str | None) -> bool:
        """True when ``directory`` is (under) one of ``capture.exclude_paths``."""
        if not directory:
            return False
        target = Path(directory).expanduser()
        return any(
            target == root or root in target.parents for root in self.config.capture.exclude_paths
        )

    def _configured_repo(self, directory: str | None) -> str | None:
        """Name of the configured repo containing ``directory`` (works for moved folders too)."""
        if not directory:
            return None
        target = Path(directory)
        for repo in sorted(self.config.repos, key=lambda r: len(r.path.parts), reverse=True):
            if target == repo.path or repo.path in target.parents:
                return repo.display_name
        # Folders get moved (~/Desktop/microservices-redesign → consolidated-services); match a
        # path component against configured repo names, innermost first.
        names = {repo.display_name: repo.display_name for repo in self.config.repos}
        names.update(
            {
                repo.path.name: repo.display_name
                for repo in self.config.repos
                if repo.path.name not in names
            }
        )
        for part in reversed(target.parts):
            if part in names:
                return names[part]
        return None

    # ---------------------------------------------------------------- hooks
    def from_hook(self, payload: dict[str, Any], hint: str, received_at: datetime) -> Event:
        """Normalize one hook payload."""
        source = detect_source(payload, hint)
        event_name = str(payload.get("hook_event_name", "")).replace("_", "").lower()
        kinds = _CURSOR_KINDS if source is Source.CURSOR else _CLAUDE_KINDS
        kind = kinds.get(event_name)
        if kind is None:
            raise UnsupportedPayloadError(f"unsupported {source.value} hook event {event_name!r}")
        if source is Source.CURSOR:
            session_id = payload.get("conversation_id") or payload.get("session_id")
            roots = payload.get("workspace_roots") or []
            cwd = payload.get("cwd") or (roots[0] if roots else None)
        else:
            session_id = payload.get("session_id")
            cwd = payload.get("cwd")
        if not session_id:
            raise UnsupportedPayloadError("hook payload has no session id")
        if is_own_sandbox(cwd):
            raise OwnSandboxEventError("event from WorkDigest's own LLM sandbox")
        if self.is_excluded(cwd):
            raise ExcludedPathEventError(f"{cwd} is in capture.exclude_paths")
        session_key = f"{'cursor' if source is Source.CURSOR else 'claude'}:{session_id}"
        draft = self._draft(kind, payload)
        draft.payload.setdefault("hook_event_name", payload.get("hook_event_name"))
        if payload.get("transcript_path"):
            draft.payload["transcript_path"] = payload["transcript_path"]
        if payload.get("model"):
            draft.payload["model"] = payload["model"]
        return self.build(
            source=source,
            origin=Origin.HOOK,
            kind=draft.kind,
            natural_key=draft.natural_key,
            ts=received_at,
            payload=draft.payload,
            session_key=session_key,
            cwd=str(cwd) if cwd else None,
            ticket_texts=draft.ticket_texts,
        )

    def _draft(self, kind: Kind, p: dict[str, Any]) -> _Draft:  # noqa: PLR0911
        cap = self.config.capture
        if kind is Kind.PROMPT:
            text = str(p.get("prompt") or "")
            return self.prompt_draft(text, attachments=p.get("attachments"))
        if kind is Kind.TOOL_USE:
            tool_name = str(p.get("tool_name") or "")
            tool_input = p.get("tool_input")
            output = p.get("tool_response", p.get("tool_output"))
            return self.tool_draft(str(p.get("tool_use_id") or ""), tool_name, tool_input, output)
        if kind is Kind.SHELL:
            command = str(p.get("command") or "")
            output = str(p.get("output") or "")
            draft = self.shell_payload(command, output)
            draft["duration"] = p.get("duration")
            return _Draft(
                kind,
                stable_hash(p.get("generation_id") or "", command, output[-200:]),
                draft,
                (command,),
            )
        if kind is Kind.FILE_EDIT:
            path = str(p.get("file_path") or "")
            edits = p.get("edits") or []
            added = sum(
                str(e.get("new_string", "")).count("\n") + 1 for e in edits if isinstance(e, dict)
            )
            removed = sum(
                str(e.get("old_string", "")).count("\n") + 1 for e in edits if isinstance(e, dict)
            )
            return _Draft(
                kind,
                stable_hash(p.get("generation_id") or "", path, edits),
                {
                    "file_path": path,
                    "edits": len(edits),
                    "lines_added": added,
                    "lines_removed": removed,
                },
            )
        if kind is Kind.AGENT_RESPONSE:
            text = str(p.get("text") or "")
            return _Draft(
                kind,
                stable_hash(p.get("generation_id") or "", text),
                {"text": self._safe_head(text, cap.agent_text_chars), "chars": len(text)},
                (text,),
            )
        if kind is Kind.STOP:
            message = str(p.get("last_assistant_message") or "")
            key = stable_hash(p.get("generation_id") or "", p.get("status") or "", message)
            return _Draft(
                kind,
                key,
                {
                    "status": p.get("status"),
                    "last_message": self._safe_head(message, cap.agent_text_chars),
                },
                (message,),
            )
        if kind is Kind.SUBAGENT_STOP:
            summary = str(p.get("summary") or p.get("last_assistant_message") or "")
            key = str(p.get("agent_id") or stable_hash(p.get("task") or "", summary))
            return _Draft(
                kind,
                key,
                {
                    "agent_type": p.get("agent_type") or p.get("subagent_type"),
                    "description": p.get("description") or p.get("task"),
                    "summary": self._safe_head(summary, cap.agent_text_chars),
                    "modified_files": p.get("modified_files"),
                },
                (summary,),
            )
        if kind is Kind.SESSION_START:
            return _Draft(
                kind,
                str(p.get("source") or p.get("composer_mode") or "start"),
                {
                    "start_source": p.get("source"),
                    "title": p.get("session_title"),
                    "composer_mode": p.get("composer_mode"),
                    "is_background_agent": p.get("is_background_agent"),
                },
            )
        # SESSION_END
        return _Draft(
            kind,
            str(p.get("reason") or p.get("final_status") or "end"),
            {
                "reason": p.get("reason"),
                "final_status": p.get("final_status"),
                "duration_ms": p.get("duration_ms"),
            },
        )

    # ----------------------------------------------- shared payload builders
    def _safe_head(self, text: str, limit: int) -> str:
        """Redact, then truncate: cutting first could leave half a secret unrecognised."""
        return _head(self.redactor.redact_text(text).value, limit)

    def _safe_tail(self, text: str, limit: int) -> str:
        """Redact, then keep the end of ``text``."""
        return _tail(self.redactor.redact_text(text).value, limit)

    def prompt_draft(self, text: str, attachments: Any = None) -> _Draft:
        """Payload for a user prompt (shared by hooks and transcripts)."""
        normalized = normalize_prompt(text)
        payload: dict[str, Any] = {
            "excerpt": self._safe_head(text, self.config.capture.prompt_excerpt_chars),
            "chars": len(text),
        }
        if isinstance(attachments, list):
            payload["attachments"] = [
                a.get("file_path")
                for a in attachments
                if isinstance(a, dict) and a.get("file_path")
            ]
        return _Draft(Kind.PROMPT, stable_hash(normalized), payload, (text,))

    def tool_draft(self, tool_use_id: str, tool_name: str, tool_input: Any, output: Any) -> _Draft:
        """Payload for a tool call (shared by hooks and transcripts)."""
        name = tool_name.lower()
        args = tool_input if isinstance(tool_input, dict) else {}
        payload: dict[str, Any] = {"tool_name": tool_name}
        ticket_texts: tuple[str | None, ...] = ()
        if name in _SHELL_TOOLS:
            command = str(args.get("command") or "")
            payload.update(self.shell_payload(command, _output_text(output)))
            if args.get("description"):
                payload["description"] = self._safe_head(str(args["description"]), 200)
            ticket_texts = (command,)
        else:
            path = next((str(args[k]) for k in _PATH_KEYS if args.get(k)), None)
            if path:
                payload["file_path"] = path
            if name in _EDIT_TOOLS:
                payload["is_edit"] = True
            for key in ("pattern", "description", "subagent_type", "url", "query"):
                if args.get(key):
                    payload[key] = self._safe_head(str(args[key]), 300)
            if isinstance(output, dict) and "interrupted" in output:
                payload["interrupted"] = output["interrupted"]
        natural_key = tool_use_id or stable_hash(tool_name, args)
        return _Draft(Kind.TOOL_USE, natural_key, payload, ticket_texts)

    def shell_payload(self, command: str, output: str) -> dict[str, Any]:
        """Command and output tail, with output dropped when it touches secret files."""
        cap = self.config.capture
        payload: dict[str, Any] = {"command": self._safe_head(command, cap.command_chars)}
        if self.redactor.mentions_sensitive_path(command):
            payload["output_dropped"] = "sensitive_path"
            return payload
        if output:
            # Parse the full output before truncating: `go test -v` counts need every line.
            result = parse_test_output(command, output)
            if result is not None:
                payload["test_result"] = result.model_dump()
            payload["output_tail"] = self._safe_tail(output, cap.tool_output_tail_chars)
            payload["output_chars"] = len(output)
        return payload


def _output_text(output: Any) -> str:
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    if isinstance(output, dict):
        parts = [str(output.get(key) or "") for key in ("stdout", "stderr", "output")]
        return "\n".join(part for part in parts if part)
    if isinstance(output, list):
        return "\n".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in output
        )
    return str(output)


def _head(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = limit
    for marker in _REDACTION_MARKER.finditer(text):
        if marker.start() < cut < marker.end():
            cut = marker.end()  # never split a redaction marker; it records the rule
    return text[:cut] + "…"


def _tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = len(text) - limit
    for marker in _REDACTION_MARKER.finditer(text):
        if marker.start() < cut < marker.end():
            cut = marker.start()
    return "…" + text[cut:]


def received_at_of(record: dict[str, Any]) -> datetime | None:
    """Parse the spool record's receive timestamp."""
    value = record.get("received_at")
    return parse_iso(value) if isinstance(value, str) else None
