"""Poll configured git repositories for my commits and new branches.

Git is the ground truth for what changed. The poller records:

* commits whose author email is one of mine, inside the backfill window, with the
  local branches that contain them, whether a remote branch contains them (pushed),
  and their diffstat;
* new local branches. The first poll of a repo records a silent baseline, and later
  polls emit ``branch_created`` events, using the reflog's creation time when available.

It only runs read-only git commands (``log``, ``for-each-ref``, ``reflog``, ``remote``).
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from workdigest.clock import local_tz, now_local, parse_iso, work_date
from workdigest.config import Config, RepoConfig
from workdigest.ingest.normalize import EventFactory
from workdigest.ingest.sink import record
from workdigest.models import Kind, Origin, Source, canonical_json
from workdigest.store.db import Store

_GIT_TIMEOUT_S = 20
_FIELD = "\x1f"
_RECORD = "\x1e"
_LOG_FORMAT = _RECORD + _FIELD.join(["%H", "%an", "%ae", "%aI", "%cI", "%s"])
_SSH_REMOTE = re.compile(r"^(?:ssh://)?git@(?P<host>[^:/]+)[:/](?P<path>.+?)(?:\.git)?/?$")
_HTTPS_REMOTE = re.compile(r"^https?://(?:[^@/]+@)?(?P<host>[^/]+)/(?P<path>.+?)(?:\.git)?/?$")


class GitError(RuntimeError):
    """A git command failed."""


def run_git(repo: Path, *args: str) -> str:
    """Run a read-only git command in ``repo`` and return stdout."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), "--no-pager", *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"git {' '.join(args)}: {exc}") from exc
    if completed.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {completed.stderr.strip()[:500]}")
    return completed.stdout


def web_url_for(remote_url: str | None) -> str | None:
    """Turn a git remote into a browsable https URL (Bitbucket, GitHub, GitLab)."""
    if not remote_url:
        return None
    for pattern in (_SSH_REMOTE, _HTTPS_REMOTE):
        match = pattern.match(remote_url.strip())
        if match:
            return f"https://{match['host']}/{match['path']}"
    return None


def branch_url(web_url: str | None, branch: str) -> str | None:
    """Link to a branch's commit list (Bitbucket format; GitHub uses /commits/<branch>)."""
    if not web_url:
        return None
    encoded = quote(branch, safe="")
    if "bitbucket.org" in web_url:
        return f"{web_url}/commits/branch/{encoded}"
    return f"{web_url}/commits/{encoded}"


@dataclass(slots=True)
class PollStats:
    """Counters for one poll."""

    repos: int = 0
    commits_new: int = 0
    commits_seen: int = 0
    branches_new: int = 0
    errors: list[str] = field(default_factory=list)


def poll_repos(
    store: Store, factory: EventFactory, config: Config, now: datetime | None = None
) -> PollStats:
    """Poll every configured repository; one failing repo never stops the others."""
    stats = PollStats()
    current = now or now_local()
    since = current - timedelta(days=config.sources.backfill_days + 1)
    for repo in config.repos:
        try:
            _poll_repo(
                store=store, factory=factory, config=config, repo=repo, since=since, stats=stats
            )
            stats.repos += 1
        except GitError as exc:
            message = f"{repo.display_name}: {exc}"
            stats.errors.append(message)
            store.log_error("git.poll", message, str(repo.path))
    return stats


def _poll_repo(
    *,
    store: Store,
    factory: EventFactory,
    config: Config,
    repo: RepoConfig,
    since: datetime,
    stats: PollStats,
) -> None:
    if not (repo.path / ".git").exists() and not repo.path.joinpath("HEAD").exists():
        raise GitError(f"not a git repository: {repo.path}")
    name = repo.display_name
    try:
        remote = run_git(repo.path, "remote", "get-url", "origin").strip() or None
    except GitError:
        remote = None
    web = web_url_for(remote)
    with store.transaction():
        store.upsert_repo(name, str(repo.path), remote, web)
        _poll_branches(store=store, factory=factory, repo=repo, name=name, web=web, stats=stats)
        _poll_commits(
            store=store,
            factory=factory,
            config=config,
            repo=repo,
            name=name,
            since=since,
            stats=stats,
        )


def _poll_branches(
    *,
    store: Store,
    factory: EventFactory,
    repo: RepoConfig,
    name: str,
    web: str | None,
    stats: PollStats,
) -> None:
    output = run_git(
        repo.path,
        "for-each-ref",
        "refs/heads",
        f"--format=%(refname:short){_FIELD}%(objectname){_FIELD}%(upstream:short)",
    )
    known = store.known_branches(name)
    first_poll = not known
    for line in output.splitlines():
        if not line.strip():
            continue
        branch, head, upstream = [*line.split(_FIELD), "", ""][:3]
        if branch in known:
            store.upsert_branch(name, branch, head, upstream or None, None, baseline=False)
            continue
        created = None if first_poll else _branch_created_at(repo.path, branch)
        store.upsert_branch(name, branch, head, upstream or None, created, baseline=first_poll)
        if first_poll:
            continue
        stats.branches_new += 1
        ts = parse_iso(created or "") or now_local()
        record(
            store,
            factory.build(
                source=Source.GIT,
                origin=Origin.POLLER,
                kind=Kind.BRANCH_CREATED,
                natural_key=f"{name}:{branch}",
                ts=ts,
                ts_approx=created is None,
                payload={
                    "branch": branch,
                    "head": head,
                    "upstream": upstream or None,
                    "url": branch_url(web, branch),
                },
                repo=name,
                branch=branch,
                ticket_texts=(branch,),
            ),
        )


def _branch_created_at(repo: Path, branch: str) -> str | None:
    try:
        output = run_git(
            repo,
            "reflog",
            "show",
            "--date=iso-strict",
            "--format=%gd%x1f%gs",
            f"refs/heads/{branch}",
        )
    except GitError:
        return None
    for line in reversed(output.splitlines()):
        selector, _, subject = line.partition(_FIELD)
        if subject.startswith("branch: Created from"):
            match = re.search(r"@\{(?P<date>[^}]+)\}", selector)
            return match["date"] if match else None
    return None


def _poll_commits(
    *,
    store: Store,
    factory: EventFactory,
    config: Config,
    repo: RepoConfig,
    name: str,
    since: datetime,
    stats: PollStats,
) -> None:
    emails = set(config.identity.author_emails)
    if not emails:
        raise GitError("identity.author_emails is empty; cannot tell which commits are mine")
    author_args = [f"--author={re.escape(email)}" for email in sorted(emails)]
    output = run_git(
        repo.path,
        "log",
        "--branches",
        "--remotes",
        "--no-merges",
        f"--since={since.isoformat()}",
        *author_args,
        f"--format={_LOG_FORMAT}",
        "--numstat",
    )
    for chunk in output.split(_RECORD):
        if not chunk.strip():
            continue
        header, _, numstat = chunk.partition("\n")
        parts = header.split(_FIELD)
        if len(parts) != 6:
            continue
        sha, author_name, author_email, authored, committed, subject = parts
        if author_email.lower() not in emails:
            continue  # --author is a regex over "name <email>"; re-check exactly
        authored_at = parse_iso(authored)
        if authored_at is None:
            continue
        files, insertions, deletions = _numstat(numstat)
        branches = _containing(repo.path, sha, "refs/heads")
        pushed = bool(_containing(repo.path, sha, "refs/remotes"))
        tickets = factory.tickets.find(subject, *branches)
        day = work_date(authored_at, config.day.boundary_hour).isoformat()
        is_new = store.upsert_commit(
            {
                "repo": name,
                "sha": sha,
                "author_name": author_name,
                "author_email": author_email.lower(),
                "authored_at": authored_at.isoformat(),
                "committed_at": committed,
                "work_date": day,
                "subject": factory.redactor.redact_text(subject).value,
                "branches": canonical_json(branches),
                "pushed": int(pushed),
                "files_changed": files,
                "insertions": insertions,
                "deletions": deletions,
                "ticket_keys": canonical_json(tickets),
            }
        )
        stats.commits_seen += 1
        if is_new:
            stats.commits_new += 1
            record(
                store,
                factory.build(
                    source=Source.GIT,
                    origin=Origin.POLLER,
                    kind=Kind.COMMIT,
                    natural_key=f"{name}:{sha}",
                    ts=authored_at.astimezone(local_tz()),
                    payload={
                        "sha": sha,
                        "subject": subject,
                        "files_changed": files,
                        "insertions": insertions,
                        "deletions": deletions,
                    },
                    repo=name,
                    branch=branches[0] if branches else None,
                    ticket_texts=(subject, *branches),
                ),
            )


def _numstat(text: str) -> tuple[int, int, int]:
    files = insertions = deletions = 0
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        files += 1
        insertions += int(parts[0]) if parts[0].isdigit() else 0  # "-" for binary files
        deletions += int(parts[1]) if parts[1].isdigit() else 0
    return files, insertions, deletions


def _containing(repo: Path, sha: str, namespace: str) -> list[str]:
    output = run_git(
        repo, "for-each-ref", "--contains", sha, "--format=%(refname:short)", namespace
    )
    return [
        line.strip() for line in output.splitlines() if line.strip() and not line.endswith("/HEAD")
    ]
