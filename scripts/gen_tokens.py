#!/usr/bin/env python3
"""Generate the website's CSS and the app's Dart constants from design/tokens.json.

Usage:
    python3 scripts/gen_tokens.py          # write web/shared/tokens.css and app/lib/core/theme/tokens.g.dart
    python3 scripts/gen_tokens.py --check  # exit 1 when either file is stale
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "design" / "tokens.json"
TARGET = ROOT / "web" / "shared" / "tokens.css"
DART_TARGET = ROOT / "app" / "lib" / "core" / "theme" / "tokens.g.dart"
_RGBA = re.compile(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)")


def _colors(values: dict[str, str]) -> list[str]:
    return [f"  --wd-{name}: {value};" for name, value in values.items()]


def render(tokens: dict) -> str:
    """The CSS text: dark is the default, light follows the system unless the page says otherwise."""
    motion = tokens["motion"]
    common = [
        *(f"  --wd-space-{k}: {v}px;" for k, v in tokens["space"].items()),
        *(f"  --wd-radius-{k}: {v}px;" for k, v in tokens["radius"].items()),
        *(f"  --wd-{k}: {v}ms;" for k, v in motion.items() if isinstance(v, int)),
        *(f"  --wd-{k}: cubic-bezier({', '.join(map(str, v))});" for k, v in motion.items() if isinstance(v, list)),
        f"  --wd-font: '{tokens['font']['family']}', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;",
    ]
    dark = _colors(tokens["color"]["dark"])
    light = _colors(tokens["color"]["light"])
    lines = [
        "/* Generated from design/tokens.json by scripts/gen_tokens.py. Do not edit. */",
        ":root {",
        "  color-scheme: dark;",
        *common,
        *dark,
        "}",
        "@media (prefers-color-scheme: light) {",
        "  :root:not([data-theme='dark']) {",
        "    color-scheme: light;",
        *(f"  {line}" for line in light),
        "  }",
        "}",
        ":root[data-theme='light'] {",
        "  color-scheme: light;",
        *light,
        "}",
        "",
    ]
    return "\n".join(lines)


def _argb(value: str) -> str:
    """'#0E0D12' → 0xFF0E0D12; 'rgba(26, 24, 32, 0.80)' → 0xCC1A1820."""
    if value.startswith("#"):
        return f"0xFF{value[1:].upper()}"
    match = _RGBA.fullmatch(value)
    if not match:
        raise ValueError(f"unsupported colour {value!r}")
    r, g, b = (int(match[i]) for i in (1, 2, 3))
    alpha = round(float(match[4]) * 255)
    return f"0x{alpha:02X}{r:02X}{g:02X}{b:02X}"


def _camel(name: str) -> str:
    head, *rest = name.split("-")
    return head + "".join(part.capitalize() for part in rest)


def render_dart(tokens: dict) -> str:
    """Dart constants the Flutter theme reads."""
    lines = [
        "// Generated from design/tokens.json by scripts/gen_tokens.py. Do not edit.",
        "// ignore_for_file: public_member_api_docs",
        "import 'package:flutter/animation.dart';",
        "",
        "abstract final class AuroraTokens {",
    ]
    for scheme in ("dark", "light"):
        for name, value in tokens["color"][scheme].items():
            field = _camel(name)
            lines.append(f"  static const {scheme}{field[0].upper()}{field[1:]} = Color({_argb(value)});")
    for name, value in tokens["space"].items():
        lines.append(f"  static const double space{name.upper() if len(name) <= 3 else name.capitalize()} = {value};")
    for name, value in tokens["radius"].items():
        lines.append(f"  static const double radius{name.capitalize()} = {value};")
    for name, value in tokens["motion"].items():
        if isinstance(value, int):
            lines.append(f"  static const Duration {name} = Duration(milliseconds: {value});")
        else:
            lines.append(f"  static const Cubic {name} = Cubic({', '.join(str(v) for v in value)});")
    lines += ["}", ""]
    return "\n".join(lines)


def main() -> int:
    """Entry point."""
    tokens = json.loads(SOURCE.read_text(encoding="utf-8"))
    outputs = {TARGET: render(tokens), DART_TARGET: render_dart(tokens)}
    if "--check" in sys.argv:
        stale = [p for p, text in outputs.items() if not p.exists() or p.read_text(encoding="utf-8") != text]
        for path in stale:
            print(f"ERROR: {path.relative_to(ROOT)} is stale; run `make tokens`", file=sys.stderr)  # noqa: T201
        return 1 if stale else 0
    for path, text in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
