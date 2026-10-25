"""Test-runner output parsing: the source of every pass/total number in the digest."""

from __future__ import annotations

import pytest

from workdigest.ingest.test_parsers import looks_like_test_command, parse_test_output

CASES = [
    (
        "vitest-pass",
        "npx vitest run",
        " Test Files  52 passed (52)\n      Tests  644 passed (644)\n   Start at  10:00\n",
        ("vitest", 644, 0, 0, 644, "tests"),
    ),
    (
        "vitest-fail",
        "npm test",
        "      Tests  2 failed | 640 passed | 2 skipped (644)\n",
        ("vitest", 640, 2, 2, 644, "tests"),
    ),
    (
        "jest",
        "npm run test",
        "Test Suites: 1 failed, 9 passed, 10 total\nTests:       1 failed, 5 passed, 6 total\n",
        ("jest", 5, 1, 0, 6, "tests"),
    ),
    (
        "karma",
        "ng test --watch=false",
        "Chrome Headless 120: Executed 966 of 966 SUCCESS (41.2 secs / 39.8 secs)\nTOTAL: 966 SUCCESS\n",
        ("karma", 966, 0, 0, 966, "tests"),
    ),
    (
        "karma-fail",
        "ng test",
        "Chrome Headless: Executed 970 of 971 (3 FAILED) (skipped 1) (40 secs)\n",
        ("karma", 967, 3, 1, 971, "tests"),
    ),
    (
        "pytest",
        "uv run pytest -q",
        "........\n==== 10 passed, 2 failed, 1 skipped in 1.23s ====\n",
        ("pytest", 10, 2, 1, 13, "tests"),
    ),
    (
        "pytest-errors",
        "pytest",
        "=========== 3 passed, 1 error in 0.40s ===========\n",
        ("pytest", 3, 1, 0, 4, "tests"),
    ),
    (
        "flutter-pass",
        "flutter test",
        "00:12 +42: All tests passed!\n",
        ("flutter", 42, 0, 0, 42, "tests"),
    ),
    (
        "flutter-fail",
        "flutter test",
        "00:12 +40 ~1 -2: Some tests failed.\n",
        ("flutter", 40, 2, 1, 43, "tests"),
    ),
    (
        "playwright",
        "npx playwright test",
        "Running 22 tests using 4 workers\n\n  2 failed\n  20 passed (35.2s)\n",
        ("playwright", 20, 2, 0, 22, "tests"),
    ),
    ("mocha", "npm test", "\n  12 passing (2s)\n  1 failing\n", ("mocha", 12, 1, 0, 13, "tests")),
    (
        "go-verbose",
        "go test -v ./internal/otp/...",
        "=== RUN   TestA\n--- PASS: TestA (0.00s)\n--- PASS: TestB (0.00s)\n--- FAIL: TestC (0.01s)\n"
        "--- SKIP: TestD (0.00s)\nFAIL\nFAIL\tgithub.com/ei/identity/internal/otp\t0.412s\n",
        ("go", 2, 1, 1, 4, "tests"),
    ),
    (
        "go-packages",
        "go test ./...",
        "ok  \tgithub.com/ei/billing/a\t0.3s\nok  \tgithub.com/ei/billing/b\t(cached)\n"
        "?   \tgithub.com/ei/billing/cmd\t[no test files]\nFAIL\tgithub.com/ei/billing/c [build failed]\n",
        ("go", 2, 1, 0, 3, "packages"),
    ),
]


@pytest.mark.parametrize(
    ("case", "command", "output", "expected"), CASES, ids=[c[0] for c in CASES]
)
def test_runner_outputs(
    case: str, command: str, output: str, expected: tuple[str, int, int, int, int, str]
) -> None:
    """TC-TST-001: Each supported runner's summary parses to exact counts.

    Given: real-format output from vitest, jest, karma, pytest, flutter, playwright, mocha and go.
    When: parse_test_output() runs with the matching command.
    Then: runner, passed, failed, skipped, total and unit match exactly.
    """
    result = parse_test_output(command, output)
    assert result is not None, case
    assert (
        result.runner,
        result.passed,
        result.failed,
        result.skipped,
        result.total,
        result.unit,
    ) == expected


def test_summary_line_is_kept_verbatim() -> None:
    """TC-TST-002: The exact summary line is kept as evidence.

    Given: vitest output with ANSI colour codes.
    When: parsed.
    Then: summary_line is the clean summary text.
    """
    result = parse_test_output("npm test", "\x1b[32m      Tests  966 passed (966)\x1b[0m\n")
    assert result is not None
    assert result.summary_line == "Tests  966 passed (966)"
    assert result.ok


def test_last_summary_wins_for_reruns() -> None:
    """TC-TST-003: When output holds several runs (watch mode or a retry), the last one counts.

    Given: two vitest summaries, a failing run then a passing one.
    When: parsed.
    Then: the result reflects the final passing run.
    """
    output = "      Tests  1 failed | 643 passed (644)\n...\n      Tests  644 passed (644)\n"
    result = parse_test_output("npx vitest", output)
    assert result is not None
    assert (result.passed, result.failed) == (644, 0)


@pytest.mark.parametrize(
    ("command", "output"),
    [
        ("cat old-results.log", "      Tests  644 passed (644)\n"),
        ("git log", "Executed 966 of 966 SUCCESS"),
        ("npm test", "no summary here at all"),
        ("npm test", ""),
    ],
)
def test_non_test_commands_and_noise_are_ignored(command: str, output: str) -> None:
    """TC-TST-004: Only real test commands with a recognisable summary produce a result.

    Given: summaries printed by non-test commands, and test commands with no summary.
    When: parsed.
    Then: no result is produced, so stale or unrelated numbers never become evidence.
    """
    assert parse_test_output(command, output) is None


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("npm test", True),
        ("go test ./...", True),
        ("flutter test", True),
        ("ng test", True),
        ("make e2e", True),
        ("npm run build", False),
        ("ls -la", False),
    ],
)
def test_test_command_detection(command: str, expected: bool) -> None:
    """TC-TST-005: Test commands are recognised by keyword.

    Given: common test and non-test commands.
    When: looks_like_test_command() runs.
    Then: only the test commands match.
    """
    assert looks_like_test_command(command) is expected
