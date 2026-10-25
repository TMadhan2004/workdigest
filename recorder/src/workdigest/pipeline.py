"""Wires collectors together for one ingest pass (used by the CLI and the daemon)."""

from __future__ import annotations

import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from workdigest.collectors.claude_transcripts import collect_claude_transcripts
from workdigest.collectors.cursor_transcripts import collect_cursor_transcripts
from workdigest.collectors.git_poller import poll_repos
from workdigest.config import Config
from workdigest.ingest.context import RepoResolver, TicketMatcher
from workdigest.ingest.normalize import EventFactory
from workdigest.ingest.spool import ingest_spool
from workdigest.paths import Paths
from workdigest.redact.redactor import Redactor
from workdigest.secrets import SecretStore
from workdigest.store.db import Store


def build_factory(config: Config, known_secrets: tuple[str, ...] | None = None) -> EventFactory:
    """Create the shared event factory for ``config``.

    Every token stored in the Keychain is redacted as a known value unless ``known_secrets``
    is given explicitly.
    """
    if known_secrets is None:
        known_secrets = SecretStore().known_values()
    redactor = Redactor(
        extra_patterns=config.redaction.extra_patterns,
        known_secrets=known_secrets,
        sensitive_path_globs=config.redaction.sensitive_path_globs,
        safe_path_globs=config.redaction.safe_path_globs,
    )
    return EventFactory(config, redactor, TicketMatcher(config.jira.project_keys), RepoResolver())


@dataclass(slots=True)
class PassReport:
    """Outcome of one pass, per stage."""

    stages: dict[str, Any] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)


STAGES = ("spool", "claude_transcripts", "cursor_transcripts", "git")


def run_pass(
    store: Store,
    config: Config,
    paths: Paths,
    stages: tuple[str, ...] = STAGES,
    factory: EventFactory | None = None,
) -> PassReport:
    """Run the selected stages. A failing stage is logged and the others still run."""
    factory = factory or build_factory(config)
    report = PassReport()
    runners: dict[str, Callable[[], Any]] = {
        "spool": lambda: ingest_spool(store, factory, paths.spool_dir),
        "claude_transcripts": lambda: collect_claude_transcripts(
            store, factory, config.sources.claude_projects_dir, config.sources.backfill_days
        ),
        "cursor_transcripts": lambda: collect_cursor_transcripts(
            store, factory, config.sources.cursor_projects_dir, config.sources.backfill_days
        ),
        "git": lambda: poll_repos(store, factory, config),
    }
    for stage in stages:
        try:
            report.stages[stage] = runners[stage]()
        except Exception as exc:  # noqa: BLE001 - isolate stages from each other
            report.failures[stage] = f"{type(exc).__name__}: {exc}"
            store.log_error(f"pipeline.{stage}", traceback.format_exc())
    return report
