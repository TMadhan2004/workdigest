#!/usr/bin/env python3
"""Fail if a tracked file contains real names, internal repos or project ids.

The repository is public. Real people, internal repository names and project identifiers
must never be committed. Only SHA-256 prefixes of the forbidden terms are stored here, so
this file itself reveals nothing. Every word (and hyphenated token) in every tracked text
file is lower-cased, hashed and compared.

Usage:
    python3 scripts/check_public.py        # exit 1 and list file:line for each hit
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

FORBIDDEN = {
    "7fb6139f1a32a77be3ed", "fc10a833c4c55e66e058", "a97303597d9dbe88bf1e", "53efde4ff857fd39e808",
    "41b4b5bc6be63a6ef9a0", "cfd1321425fe1cd4f208", "02b03a1885b407c4df9c", "f43fe807d373fbfe837c",
    "0d176b4ebce92659ae52", "390cc9baebb6c819df6f", "a3b076d489454fbd34f6", "0bc8cb985cd197613abe",
    "5741b0bab59868b9e3ef", "03d77a8985609730c711", "7bfa8a37ff9a283a2758", "1c1a0db60c4833a15dc9",
    "841b386ab71980123ce0",
}  # fmt: skip
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*[A-Za-z0-9]")
BINARY = {".png", ".jpg", ".ico", ".ttf", ".jar", ".webp", ".apk", ".keystore"}


def _hash(token: str) -> str:
    return hashlib.sha256(token.lower().encode()).hexdigest()[:20]


def hits(text: str) -> list[int]:
    """Line numbers containing a forbidden term."""
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        tokens = set(TOKEN.findall(line))
        # Also check each part of hyphenated tokens and the joined pieces ("portal-service-v4").
        tokens |= {part for token in list(tokens) for part in token.split("-")}
        if any(_hash(token) in FORBIDDEN for token in tokens):
            found.append(number)
    return found


def main() -> int:
    """Entry point."""
    files = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    problems = []
    for name in files:
        path = Path(name)
        if path.suffix in BINARY or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        problems.extend(f"{name}:{line}" for line in hits(text))
    for problem in problems:
        print(f"ERROR: real-data term in {problem}", file=sys.stderr)  # noqa: T201
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
