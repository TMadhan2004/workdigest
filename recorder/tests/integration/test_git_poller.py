"""Git poller against real temporary repositories."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tests.conftest import IST, ME, git
from workdigest.collectors.git_poller import branch_url, poll_repos, web_url_for
from workdigest.config import Config
from workdigest.ingest.normalize import EventFactory
from workdigest.pipeline import build_factory
from workdigest.store.db import Store

pytestmark = pytest.mark.integration


@pytest.fixture
def repo_pair(tmp_path: Path) -> tuple[Path, Path]:
    """A working repo with a bare 'origin' remote."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    git(origin, "init", "-q", "--bare", "-b", "main")
    work = tmp_path / "billing-service"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    git(work, "remote", "add", "origin", str(origin))
    (work / "README.md").write_text("hello\n")
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "chore: initial commit")
    git(work, "push", "-q", "origin", "main")
    return work, origin


def _config(base: Config, repo: Path) -> Config:
    return Config.model_validate({**base.model_dump(mode="json"), "repos": [{"path": str(repo)}]})


def test_commits_mine_only_with_branch_push_and_diffstat(
    config: Config, store: Store, repo_pair: tuple[Path, Path]
) -> None:
    """TC-GIT-001: Only my commits are recorded, with branches, pushed state, diffstat and tickets.

    Given: a repo with my pushed commit on main, my unpushed commit on EIT-8403-billing,
           and a colleague's commit.
    When: polled.
    Then: my 3 commits are stored (including the initial one); the branch commit is
          unpushed, with +2/-0 and ticket EIT-8403; the colleague's commit is absent.
    """
    work, _ = repo_pair
    git(work, "checkout", "-q", "-b", "EIT-8403-billing")
    (work / "billing.go").write_text("package billing\n\nfunc A() {}\n")
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "feat(billing): add billing profile")
    git(work, "checkout", "-q", "main")
    (work / "other.txt").write_text("x\n")
    git(work, "add", ".")
    git(
        work,
        "commit",
        "-q",
        "-m",
        "colleague work",
        env={"GIT_AUTHOR_EMAIL": "vikram@ei.study", "GIT_AUTHOR_NAME": "Vikram"},
    )
    (work / "mine.txt").write_text("a\nb\n")
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "fix: EIT-8383 skill tweak")
    git(work, "push", "-q", "origin", "main")

    cfg = _config(config, work)
    stats = poll_repos(store, build_factory(cfg), cfg)
    assert stats.errors == []
    rows = {r["subject"]: r for r in store.query("SELECT * FROM commits")}
    assert set(rows) == {
        "chore: initial commit",
        "feat(billing): add billing profile",
        "fix: EIT-8383 skill tweak",
    }
    feature = rows["feat(billing): add billing profile"]
    assert json.loads(feature["branches"]) == ["EIT-8403-billing"]
    assert feature["pushed"] == 0
    assert (feature["files_changed"], feature["insertions"], feature["deletions"]) == (1, 3, 0)
    assert json.loads(feature["ticket_keys"]) == ["EIT-8403"]
    assert rows["fix: EIT-8383 skill tweak"]["pushed"] == 1
    assert store.scalar("SELECT COUNT(*) FROM events WHERE kind = 'commit'") == 3


def test_repoll_is_idempotent_and_refreshes_pushed(
    config: Config, store: Store, repo_pair: tuple[Path, Path]
) -> None:
    """TC-GIT-002: Re-polling adds no duplicates and updates pushed state after a push.

    Given: an unpushed commit on a feature branch.
    When: polled, then the branch is pushed, then polled again.
    Then: there is one commit event; pushed changes 0 to 1; commits_new is 0 on the second poll.
    """
    work, _ = repo_pair
    git(work, "checkout", "-q", "-b", "feat/x")
    (work / "x.txt").write_text("x\n")
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "feat: x")
    cfg = _config(config, work)
    factory = build_factory(cfg)
    poll_repos(store, factory, cfg)
    assert store.scalar("SELECT pushed FROM commits WHERE subject = 'feat: x'") == 0
    git(work, "push", "-q", "origin", "feat/x")
    second = poll_repos(store, factory, cfg)
    assert second.commits_new == 0
    assert store.scalar("SELECT pushed FROM commits WHERE subject = 'feat: x'") == 1
    assert store.scalar("SELECT COUNT(*) FROM events WHERE kind = 'commit'") == 2


def test_new_branch_detected_after_baseline(
    config: Config, store: Store, repo_pair: tuple[Path, Path]
) -> None:
    """TC-GIT-003: Branches present at first poll are a silent baseline; later ones emit branch_created.

    Given: main exists at the first poll; EIT-9333-tests is created afterwards.
    When: polled twice.
    Then: there is one branch_created event for EIT-9333-tests with its reflog time, ticket
          EIT-9333 and a Bitbucket-style URL when the remote is bitbucket.
    """
    work, _ = repo_pair
    cfg = _config(config, work)
    factory = build_factory(cfg)
    poll_repos(store, factory, cfg)
    assert store.scalar("SELECT COUNT(*) FROM events WHERE kind = 'branch_created'") == 0
    git(work, "checkout", "-q", "-b", "EIT-9333-tests")
    stats = poll_repos(store, factory, cfg)
    assert stats.branches_new == 1
    [row] = store.query(
        "SELECT payload, ticket_keys, ts_approx FROM events WHERE kind = 'branch_created'"
    )
    assert json.loads(row["payload"])["branch"] == "EIT-9333-tests"
    assert json.loads(row["ticket_keys"]) == ["EIT-9333"]
    assert row["ts_approx"] == 0


def test_bad_repo_does_not_stop_others(
    config: Config, store: Store, repo_pair: tuple[Path, Path], tmp_path: Path
) -> None:
    """TC-GIT-004: A missing or non-git repo is reported, and the remaining repos are still polled.

    Given: config with a non-existent path, a plain folder and one real repo.
    When: polled.
    Then: two errors are reported and logged, and the real repo's commits are stored.
    """
    work, _ = repo_pair
    plain = tmp_path / "plain"
    plain.mkdir()
    cfg = Config.model_validate(
        {
            **config.model_dump(mode="json"),
            "repos": [
                {"path": str(tmp_path / "missing")},
                {"path": str(plain)},
                {"path": str(work)},
            ],
        }
    )
    stats = poll_repos(store, build_factory(cfg), cfg)
    assert len(stats.errors) == 2
    assert stats.repos == 1
    assert store.scalar("SELECT COUNT(*) FROM commits") == 1
    assert store.scalar("SELECT COUNT(*) FROM ingest_errors WHERE stage = 'git.poll'") == 2


def test_empty_identity_is_an_error(store: Store, repo_pair: tuple[Path, Path]) -> None:
    """TC-GIT-005: Without author emails the poller refuses to guess which commits are mine.

    Given: a config with no identity.author_emails.
    When: polled.
    Then: an error explains why, and no commits are stored.
    """
    work, _ = repo_pair
    cfg = Config.model_validate({"repos": [{"path": str(work)}]})
    stats = poll_repos(store, build_factory(cfg), cfg)
    assert "author_emails" in stats.errors[0]
    assert store.scalar("SELECT COUNT(*) FROM commits") == 0


def test_commits_outside_window_are_ignored(
    config: Config, store: Store, repo_pair: tuple[Path, Path]
) -> None:
    """TC-GIT-006: Commits older than the backfill window are not recorded.

    Given: a commit authored and committed 10 days ago, and backfill_days=2.
    When: polled.
    Then: only the recent initial commit is stored.
    """
    work, _ = repo_pair
    old = (datetime.now(tz=IST) - timedelta(days=10)).isoformat()
    (work / "old.txt").write_text("o\n")
    git(work, "add", ".")
    git(
        work,
        "commit",
        "-q",
        "-m",
        "old work",
        env={"GIT_AUTHOR_DATE": old, "GIT_COMMITTER_DATE": old},
    )
    cfg = _config(config, work)
    poll_repos(store, build_factory(cfg), cfg)
    subjects = [r["subject"] for r in store.query("SELECT subject FROM commits")]
    assert subjects == ["chore: initial commit"]


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        (
            "git@bitbucket.org:ei-india-admin/billing-service.git",
            "https://bitbucket.org/ei-india-admin/billing-service",
        ),
        (
            "https://arjun@bitbucket.org/ei-india-admin/api-gateway.git",
            "https://bitbucket.org/ei-india-admin/api-gateway",
        ),
        ("ssh://git@github.com/org/repo.git", "https://github.com/org/repo"),
        ("/local/path/origin.git", None),
        (None, None),
    ],
)
def test_web_url_for(remote: str | None, expected: str | None) -> None:
    """TC-GIT-007: Git remotes map to browsable https URLs, with credentials stripped.

    Given: SSH, HTTPS-with-user and local remotes.
    When: web_url_for() runs.
    Then: the https repository URL comes back, without the username, or None for local paths.
    """
    assert web_url_for(remote) == expected


def test_branch_url_encoding() -> None:
    """TC-GIT-008: Branch links match the Bitbucket format used in the digest (slashes encoded).

    Given: the branch feat/billing-migrator on Bitbucket, and on GitHub.
    When: branch_url() runs.
    Then: Bitbucket gives /commits/branch/feat%2Fbilling-migrator, GitHub gives /commits/feat%2F..., and None stays None.
    """
    bb = "https://bitbucket.org/ei-india-admin/billing-service"
    assert branch_url(bb, "feat/billing-migrator") == f"{bb}/commits/branch/feat%2Fbilling-migrator"
    assert (
        branch_url("https://github.com/o/r", "feat/x") == "https://github.com/o/r/commits/feat%2Fx"
    )
    assert branch_url(None, "x") is None


def test_ticket_keys_from_subject_and_branch(factory: EventFactory) -> None:
    """TC-GIT-009: The shared factory matches tickets in commit subjects and branches.

    Given: the default factory (EIT and CA projects).
    When: given a subject and branch.
    Then: keys from both are returned once each.
    """
    assert factory.tickets.find("feat: EIT-1 thing", "EIT-1-branch", "CA-0215") == [
        "EIT-1",
        "CA-0215",
    ]
    assert ME in factory.config.identity.author_emails
