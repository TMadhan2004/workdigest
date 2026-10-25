"""Time helpers and configuration loading."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.conftest import IST
from workdigest.clock import day_window, parse_cursor_timestamp, parse_iso, work_date
from workdigest.config import ConfigError, load_config, render_default_config
from workdigest.paths import Paths


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-23T05:00:00.123Z", datetime(2026, 10, 23, 5, 0, 0, 123000, tzinfo=UTC)),
        ("2026-10-23T10:30:00+05:30", datetime(2026, 10, 23, 10, 30, tzinfo=IST)),
        ("2026-10-23T05:00:00", datetime(2026, 10, 23, 5, 0, tzinfo=UTC)),
        ("garbage", None),
        ("", None),
    ],
)
def test_parse_iso(value: str, expected: datetime | None) -> None:
    """TC-CLK-001: ISO timestamps parse to aware datetimes; naive means UTC; bad input gives None.

    Given: Z-suffixed, offset, naive and invalid timestamps.
    When: parse_iso() runs.
    Then: aware datetimes come back, or None for invalid input.
    """
    assert parse_iso(value) == expected


def test_work_date_and_window() -> None:
    """TC-CLK-002: Work dates honour the boundary hour and match the day window.

    Given: boundary_hour=4 in IST.
    When: 03:59 and 04:00 on 24 Oct are mapped.
    Then: they fall on 23 Oct and 24 Oct, and the 23 Oct window is [23 Oct 04:00, 24 Oct 04:00).
    """
    assert work_date(datetime(2026, 10, 24, 3, 59, tzinfo=IST), 4, IST) == date(2026, 10, 23)
    assert work_date(datetime(2026, 10, 24, 4, 0, tzinfo=IST), 4, IST) == date(2026, 10, 24)
    start, end = day_window(date(2026, 10, 23), 4, IST)
    assert (start, end - start) == (datetime(2026, 10, 23, 4, 0, tzinfo=IST), timedelta(days=1))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "<timestamp>Wednesday, Sep 23, 2026, 9:36 AM (UTC+5:30)</timestamp>",
            datetime(2026, 9, 23, 9, 36, tzinfo=IST),
        ),
        (
            "<timestamp>Monday, Oct 19, 2026, 6:50 PM (UTC-4)</timestamp>",
            datetime(2026, 10, 19, 18, 50, tzinfo=timezone(timedelta(hours=-4))),
        ),
        ("no stamp", None),
        ("<timestamp>Funday, Foo 99, 2026, 9:36 AM (UTC+5:30)</timestamp>", None),
    ],
)
def test_parse_cursor_timestamp(text: str, expected: datetime | None) -> None:
    """TC-CLK-003: Cursor's prompt timestamp tag parses with its UTC offset.

    Given: Cursor <timestamp> tags with positive and negative offsets, plus invalid ones.
    When: parse_cursor_timestamp() runs.
    Then: the exact aware datetime comes back, or None.
    """
    assert parse_cursor_timestamp(text) == expected


def test_config_defaults_when_missing(tmp_path: Path) -> None:
    """TC-CFG-001: A missing config file yields safe defaults.

    Given: no config.toml.
    When: it is loaded.
    Then: boundary_hour=4, no repos, and EIT/CA project keys.
    """
    config = load_config(tmp_path / "nope.toml")
    assert config.day.boundary_hour == 4
    assert config.repos == []
    assert config.jira.project_keys == ["EIT", "CA"]


def test_config_rendered_template_round_trips(tmp_path: Path) -> None:
    """TC-CFG-002: The starter config written by `digest init` is valid and keeps identity.

    Given: render_default_config("Arjun K", [ARJUN.K@EI.STUDY]).
    When: written and loaded.
    Then: the email is lower-cased and the name is kept.
    """
    path = tmp_path / "config.toml"
    path.write_text(render_default_config("Arjun K", ["ARJUN.K@EI.STUDY"]))
    config = load_config(path)
    assert config.identity.author_emails == ["arjun.k@ei.study"]
    assert config.identity.name == "Arjun K"


@pytest.mark.parametrize(
    "content",
    ["this is = not [toml", "[day]\nboundary_hour = 30\n", "[unknown_section]\nx = 1\n"],
)
def test_config_rejects_invalid(tmp_path: Path, content: str) -> None:
    """TC-CFG-003: Invalid TOML, out-of-range values and unknown keys are rejected clearly.

    Given: broken syntax, boundary_hour=30 and an unknown section.
    When: loaded.
    Then: ConfigError is raised, naming the file.
    """
    path = tmp_path / "config.toml"
    path.write_text(content)
    with pytest.raises(ConfigError, match=r"config\.toml"):
        load_config(path)


def test_repo_paths_expand_user(tmp_path: Path) -> None:
    """TC-CFG-004: Repo paths expand ~ and get a display name.

    Given: a repo path "~/code/billing-service" with no name.
    When: loaded.
    Then: the path is absolute and display_name is billing-service.
    """
    path = tmp_path / "config.toml"
    path.write_text('[[repos]]\npath = "~/code/billing-service"\n')
    [repo] = load_config(path).repos
    assert repo.path.is_absolute()
    assert repo.display_name == "billing-service"


def test_paths_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TC-CFG-005: WORKDIGEST_HOME overrides the default home, and ensure() makes private dirs.

    Given: WORKDIGEST_HOME set to a temp dir.
    When: Paths.resolve().ensure() runs.
    Then: spool and bin exist with mode 0700, and the hook script path is under bin/.
    """
    monkeypatch.setenv("WORKDIGEST_HOME", str(tmp_path / "wd"))
    paths = Paths.resolve()
    paths.ensure()
    assert paths.spool_dir.stat().st_mode & 0o777 == 0o700
    assert paths.hook_script.parent == paths.bin_dir
    assert Paths.resolve(tmp_path / "x").home == tmp_path / "x"


def test_default_source_dirs_are_expanded() -> None:
    """TC-CFG-006: Default transcript directories are absolute (found by the real-data smoke test).

    Given: a config with no [sources] section.
    When: it is loaded.
    Then: claude_projects_dir and cursor_projects_dir have no "~" and point into the home folder.
    """
    sources = load_config(Path("/nonexistent/config.toml")).sources
    for directory in (sources.claude_projects_dir, sources.cursor_projects_dir):
        assert directory.is_absolute()
        assert "~" not in str(directory)
        assert str(directory).startswith(str(Path.home()))
