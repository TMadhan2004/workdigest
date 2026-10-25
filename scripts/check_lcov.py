#!/usr/bin/env python3
"""Fail when line coverage in an lcov file is below a threshold: check_lcov.py <lcov.info> <percent>."""

from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> int:
    """Entry point."""
    path, threshold = Path(sys.argv[1]), float(sys.argv[2])
    text = path.read_text(encoding="utf-8")
    found = sum(int(n) for n in re.findall(r"^LF:(\d+)$", text, re.M))
    hit = sum(int(n) for n in re.findall(r"^LH:(\d+)$", text, re.M))
    percent = 100.0 * hit / found if found else 0.0
    print(f"Line coverage: {hit}/{found} = {percent:.1f}% (required {threshold:.0f}%)")  # noqa: T201
    return 0 if percent >= threshold else 1


if __name__ == "__main__":
    sys.exit(main())
