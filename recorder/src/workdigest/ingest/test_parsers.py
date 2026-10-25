"""Extract pass and fail counts from test-runner output.

The digest reports numbers such as "644/644", and those must come from real output, so
each parser keeps the exact summary line it matched as evidence. A parser runs only
when the command looks like a test command. That keeps ``cat old-test-log.txt`` from
being reported as a fresh run.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from workdigest.models import TestRun

_TEST_COMMAND = re.compile(
    r"\b(test|tests|pytest|vitest|jest|karma|mocha|playwright|spec|e2e|ginkgo|tox|nox)\b"
    r"|\bng\s+test\b|\bgo\s+test\b|\bflutter\s+test\b|\bdart\s+test\b",
    re.IGNORECASE,
)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def looks_like_test_command(command: str) -> bool:
    """Return True when ``command`` plausibly runs a test suite."""
    return bool(_TEST_COMMAND.search(command))


def _count(pattern: str, text: str) -> int:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else 0


def _last(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    last = None
    for last in pattern.finditer(text):  # noqa: B007 - we want the final match
        pass
    return last


# vitest:  "Tests  2 failed | 642 passed (644)"   /   "Tests  644 passed (644)"
_VITEST = re.compile(r"^\s*Tests\s+(?P<body>(?:\d+ \w+(?: \| )?)+)\s*\((?P<total>\d+)\)", re.M)


def _parse_vitest(text: str) -> TestRun | None:
    match = _last(_VITEST, text)
    if not match:
        return None
    body = match["body"]
    return TestRun(
        runner="vitest",
        passed=_count(r"(\d+) passed", body),
        failed=_count(r"(\d+) failed", body),
        skipped=_count(r"(\d+) (?:skipped|todo)", body),
        total=int(match["total"]),
        summary_line=match.group(0).strip(),
    )


# jest:  "Tests:       1 failed, 5 passed, 6 total"
_JEST = re.compile(r"^\s*Tests:\s+(?P<body>.*?(?P<total>\d+) total)", re.M)


def _parse_jest(text: str) -> TestRun | None:
    match = _last(_JEST, text)
    if not match:
        return None
    body = match["body"]
    return TestRun(
        runner="jest",
        passed=_count(r"(\d+) passed", body),
        failed=_count(r"(\d+) failed", body),
        skipped=_count(r"(\d+) (?:skipped|todo)", body),
        total=int(match["total"]),
        summary_line=match.group(0).strip(),
    )


# karma (Angular):  "Executed 966 of 966 SUCCESS"  /  "Executed 960 of 966 (6 FAILED) (skipped 2)"
_KARMA = re.compile(r"^.*Executed (?P<run>\d+) of (?P<total>\d+)\b.*$", re.M)


def _parse_karma(text: str) -> TestRun | None:
    match = _last(_KARMA, text)
    if not match:
        return None
    line = match.group(0).strip()
    failed = _count(r"\((\d+) FAILED\)", line)
    skipped = _count(r"\(skipped (\d+)\)", line)
    return TestRun(
        runner="karma",
        passed=max(int(match["run"]) - failed, 0),
        failed=failed,
        skipped=skipped,
        total=int(match["total"]),
        summary_line=line,
    )


# pytest:  "===== 10 passed, 2 failed, 1 skipped in 1.23s ====="
_PYTEST = re.compile(r"^=+ (?P<body>(?:\d+ \w+(?:, )?)+) in [\d.]+s(?: \([^)]*\))? =+$", re.M)


def _parse_pytest(text: str) -> TestRun | None:
    match = _last(_PYTEST, text)
    if not match:
        return None
    body = match["body"]
    passed = _count(r"(\d+) passed", body)
    failed = _count(r"(\d+) failed", body) + _count(r"(\d+) errors?", body)
    skipped = _count(r"(\d+) (?:skipped|xfailed|xpassed|deselected)", body)
    return TestRun(
        runner="pytest",
        passed=passed,
        failed=failed,
        skipped=skipped,
        total=passed + failed + skipped,
        summary_line=match.group(0).strip("= ").strip(),
    )


# flutter / dart test:  "00:12 +42 ~1 -2: Some tests failed."  /  "+42: All tests passed!"
_DART = re.compile(
    r"\+(?P<passed>\d+)(?: ~(?P<skipped>\d+))?(?: -(?P<failed>\d+))?: "
    r"(?P<msg>All tests passed!|Some tests failed\.)"
)


def _parse_dart(text: str) -> TestRun | None:
    match = _last(_DART, text)
    if not match:
        return None
    passed = int(match["passed"])
    failed = int(match["failed"] or 0)
    skipped = int(match["skipped"] or 0)
    return TestRun(
        runner="flutter",
        passed=passed,
        failed=failed,
        skipped=skipped,
        total=passed + failed + skipped,
        summary_line=match.group(0),
    )


# playwright:  "  20 passed (35.2s)"  plus optional "  2 failed" / "  1 skipped" lines
_PLAYWRIGHT = re.compile(
    r"^\s+(?P<passed>\d+) passed \((?P<dur>[\d.]+m?s|\d+m \d+s|[\d.]+m)\)\s*$", re.M
)


def _parse_playwright(text: str) -> TestRun | None:
    match = _last(_PLAYWRIGHT, text)
    if not match:
        return None
    passed = int(match["passed"])
    failed = _count(r"(?m)^\s+(\d+) failed\s*$", text)
    skipped = _count(r"(?m)^\s+(\d+) skipped\s*$", text)
    flaky = _count(r"(?m)^\s+(\d+) flaky\s*$", text)
    return TestRun(
        runner="playwright",
        passed=passed + flaky,
        failed=failed,
        skipped=skipped,
        total=passed + flaky + failed + skipped,
        summary_line=match.group(0).strip(),
    )


# mocha:  "  12 passing (2s)"  "  1 failing"
_MOCHA = re.compile(r"^\s+(?P<passed>\d+) passing \([^)]*\)\s*$", re.M)


def _parse_mocha(text: str) -> TestRun | None:
    match = _last(_MOCHA, text)
    if not match:
        return None
    passed = int(match["passed"])
    failed = _count(r"(?m)^\s+(\d+) failing\s*$", text)
    pending = _count(r"(?m)^\s+(\d+) pending\s*$", text)
    return TestRun(
        runner="mocha",
        passed=passed,
        failed=failed,
        skipped=pending,
        total=passed + failed + pending,
        summary_line=match.group(0).strip(),
    )


# go test: "--- PASS: TestX" lines when -v is used; otherwise package-level ok/FAIL lines.
_GO_TEST_PASS = re.compile(r"^\s*--- PASS: ", re.M)
_GO_TEST_FAIL = re.compile(r"^\s*--- FAIL: ", re.M)
_GO_TEST_SKIP = re.compile(r"^\s*--- SKIP: ", re.M)
_GO_PKG_OK = re.compile(r"^ok\s+\S+\s+(?:[\d.]+s|\(cached\))", re.M)
_GO_PKG_FAIL = re.compile(r"^FAIL\s+\S+\s+[\d.]+s", re.M)
_GO_PKG_BUILD_FAIL = re.compile(r"^FAIL\s+\S+\s+\[(?:build|setup) failed\]", re.M)


def _parse_go(text: str) -> TestRun | None:
    pkg_ok = len(_GO_PKG_OK.findall(text))
    pkg_fail = len(_GO_PKG_FAIL.findall(text)) + len(_GO_PKG_BUILD_FAIL.findall(text))
    if pkg_ok + pkg_fail == 0:
        return None
    test_pass = len(_GO_TEST_PASS.findall(text))
    test_fail = len(_GO_TEST_FAIL.findall(text))
    test_skip = len(_GO_TEST_SKIP.findall(text))
    summary = f"go test: {pkg_ok} ok, {pkg_fail} FAIL packages"
    if test_pass + test_fail:
        return TestRun(
            runner="go",
            passed=test_pass,
            failed=test_fail,
            skipped=test_skip,
            total=test_pass + test_fail + test_skip,
            unit="tests",
            summary_line=f"{summary}; {test_pass} passed, {test_fail} failed tests",
        )
    return TestRun(
        runner="go",
        passed=pkg_ok,
        failed=pkg_fail,
        total=pkg_ok + pkg_fail,
        unit="packages",
        summary_line=summary,
    )


# Order matters: specific formats before generic ones.
_PARSERS: tuple[Callable[[str], TestRun | None], ...] = (
    _parse_vitest,
    _parse_jest,
    _parse_karma,
    _parse_pytest,
    _parse_dart,
    _parse_playwright,
    _parse_mocha,
    _parse_go,
)


def parse_test_output(command: str, output: str) -> TestRun | None:
    """Return the test result in ``output`` when ``command`` is a test command."""
    if not output or not looks_like_test_command(command):
        return None
    clean = _ANSI.sub("", output)
    for parser in _PARSERS:
        result = parser(clean)
        if result is not None and result.total > 0:
            return result
    return None
