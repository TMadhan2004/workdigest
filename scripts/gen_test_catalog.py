#!/usr/bin/env python3
"""Generate docs/test-cases.md from test docstrings, or check that it is current.

Every test function must have a docstring whose first line is ``TC-<AREA>-<NNN>: <title>``,
followed by ``Given:``, ``When:`` and ``Then:`` lines. IDs must be unique.

Usage:
    python3 scripts/gen_test_catalog.py           # rewrite docs/test-cases.md
    python3 scripts/gen_test_catalog.py --check   # exit 1 if missing IDs or out of date
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DIRS = [ROOT / "recorder" / "tests"]
DART_TEST_DIRS = [ROOT / "app" / "test", ROOT / "app" / "integration_test"]
# Titles in single or double quotes (double when the title has an apostrophe).
DART_TEST = re.compile(
    r"""(?:test|testWidgets)\(\s*(?:'(TC-[A-Z][A-Z0-9]*-\d{3}): ((?:[^'\\]|\\.)+)'"""
    r"""|"(TC-[A-Z][A-Z0-9]*-\d{3}): ((?:[^"\\]|\\.)+)")"""
)
DART_TEST_START = re.compile(r"^\s*(?:test|testWidgets)\(")
DART_COMMENT = re.compile(r"^\s*//\s*(Given|When|Then):\s*(.*)$")
DART_CONTINUATION = re.compile(r"^\s*//\s{2,}(.*)$")
OUTPUT = ROOT / "docs" / "test-cases.md"
TC_LINE = re.compile(r"^(TC-[A-Z][A-Z0-9]*-\d{3}): (.+)$")
SECTION = re.compile(r"^(Given|When|Then):\s*(.*)$")


@dataclass(frozen=True)
class Case:
    """One catalogued test case."""

    tc_id: str
    title: str
    given: str
    when: str
    then: str
    location: str


def _parse_doc(doc: str) -> tuple[str, str, dict[str, str]] | None:
    lines = [line.strip() for line in doc.strip().splitlines()]
    match = TC_LINE.match(lines[0]) if lines else None
    if not match:
        return None
    sections: dict[str, str] = {}
    current = None
    for line in lines[1:]:
        found = SECTION.match(line)
        if found:
            current = found.group(1).lower()
            sections[current] = found.group(2)
        elif current and line:
            sections[current] += " " + line
    return match.group(1), match.group(2), sections


def collect() -> tuple[list[Case], list[str]]:
    """Return the catalogued cases and a list of problems."""
    cases: list[Case] = []
    problems: list[str] = []
    for base in TEST_DIRS:
        for path in sorted(base.rglob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) or not node.name.startswith("test_"):
                    continue
                location = f"{path.relative_to(ROOT)}::{node.name}"
                parsed = _parse_doc(ast.get_docstring(node) or "")
                if parsed is None:
                    problems.append(f"{location}: docstring must start with 'TC-AREA-NNN: title'")
                    continue
                tc_id, title, sections = parsed
                missing = [key for key in ("given", "when", "then") if key not in sections]
                if missing:
                    problems.append(f"{location}: missing {', '.join(missing)}")
                cases.append(
                    Case(tc_id, title, sections.get("given", ""), sections.get("when", ""), sections.get("then", ""), location)
                )
    cases.extend(_dart_cases(problems))
    seen: dict[str, str] = {}
    for case in cases:
        if case.tc_id in seen:
            problems.append(f"duplicate {case.tc_id}: {seen[case.tc_id]} and {case.location}")
        seen[case.tc_id] = case.location
    return sorted(cases, key=lambda c: c.tc_id), problems


def _dart_cases(problems: list[str]) -> list[Case]:
    """Flutter tests: `test('TC-APP-NNN: title', …)` followed by `// Given/When/Then:` comments."""
    found: list[Case] = []
    for base in DART_TEST_DIRS:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*_test.dart")):
            lines = path.read_text(encoding="utf-8").splitlines()
            for index, line in enumerate(lines):
                match = DART_TEST.search(line)
                if not match:
                    if DART_TEST_START.match(line):
                        problems.append(
                            f"{path.relative_to(ROOT)}:{index + 1}: test( must be followed by 'TC-AREA-NNN: title' on the same line"
                        )
                    continue
                sections: dict[str, str] = {}
                current = None
                for follow in lines[index + 1 : index + 14]:
                    comment = DART_COMMENT.match(follow)
                    if comment:
                        current = comment.group(1).lower()
                        sections[current] = comment.group(2)
                        continue
                    continuation = DART_CONTINUATION.match(follow)
                    if continuation and current:
                        sections[current] += " " + continuation.group(1).strip()
                        continue
                    if sections:
                        break
                location = f"{path.relative_to(ROOT)}:{index + 1}"
                missing = [key for key in ("given", "when", "then") if key not in sections]
                if missing and "integration_test" not in str(path):
                    problems.append(f"{location}: missing {', '.join(missing)}")
                found.append(
                    Case(match.group(1) or match.group(3), (match.group(2) or match.group(4)).replace("\\'", "'").replace('\\"', '"'), sections.get("given", ""),
                         sections.get("when", ""), sections.get("then", ""), location)
                )
    return found


def render(cases: list[Case]) -> str:
    """Render the Markdown catalog."""
    def cell(text: str) -> str:
        return text.replace("|", "\\|").replace("\n", " ")

    areas: dict[str, list[Case]] = {}
    for case in cases:
        areas.setdefault(case.tc_id.split("-")[1], []).append(case)
    out = [
        "# Test case catalog",
        "",
        "Generated by `scripts/gen_test_catalog.py` from test docstrings. Do not edit by hand.",
        f"Total: {len(cases)} test cases (parametrized tests count once).",
        "",
    ]
    for area in sorted(areas):
        out += [f"## {area}", "", "| ID | Title | Given | When | Then | Test |", "|---|---|---|---|---|---|"]
        for c in areas[area]:
            out.append(
                f"| {c.tc_id} | {cell(c.title)} | {cell(c.given)} | {cell(c.when)} | {cell(c.then)} | `{c.location}` |"
            )
        out.append("")
    return "\n".join(out)


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the catalog is stale or IDs are missing")
    args = parser.parse_args()
    cases, problems = collect()
    for problem in problems:
        print(f"ERROR: {problem}", file=sys.stderr)  # noqa: T201
    text = render(cases)
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != text:
            print("ERROR: docs/test-cases.md is stale; run scripts/gen_test_catalog.py", file=sys.stderr)  # noqa: T201
            return 1
        return 1 if problems else 0
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)} ({len(cases)} cases)")  # noqa: T201
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
