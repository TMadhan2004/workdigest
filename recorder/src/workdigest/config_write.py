"""Write ``config.toml`` from code (the setup wizard), validated before anything is saved.

The file is re-emitted from its parsed contents, so hand-written comments are not kept; the
values are. Only the types the config uses are supported: strings, booleans, integers, lists
of strings, tables and arrays of tables (``[[repos]]``).
"""

from __future__ import annotations

import json
import os
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from workdigest.config import Config

HEADER = (
    "# WorkDigest configuration (written by `digest setup`; edit it, then run `digest doctor`).\n"
)


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, Path):
        return json.dumps(str(value), ensure_ascii=False)
    if isinstance(value, list) and all(not isinstance(v, dict) for v in value):
        return "[" + ", ".join(_value(v) for v in value) + "]"
    raise TypeError(f"cannot write {type(value).__name__} to config.toml")


def dumps(data: dict[str, Any]) -> str:
    """TOML text for ``data`` (top-level scalars first, then tables, then arrays of tables)."""
    out = [HEADER]
    scalars = {k: v for k, v in data.items() if not isinstance(v, dict) and not _is_table_array(v)}
    out.extend(f"{key} = {_value(value)}" for key, value in scalars.items())
    for key, value in data.items():
        if isinstance(value, dict):
            out.append(f"\n[{key}]")
            out.extend(f"{k} = {_value(v)}" for k, v in value.items() if v is not None)
    for key, value in data.items():
        if _is_table_array(value):
            for row in value:
                out.append(f"\n[[{key}]]")
                out.extend(f"{k} = {_value(v)}" for k, v in row.items() if v is not None)
    return "\n".join(out) + "\n"


def _is_table_array(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)


def read_raw(path: Path) -> dict[str, Any]:
    """The file's contents as plain data (empty when missing)."""
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def update(path: Path, tables: dict[str, dict[str, Any]] | None = None, **top: Any) -> Config:
    """Merge values into the file, validate, and write it atomically (mode 600).

    ``tables`` is like ``{"digest": {"manager_name": "Priya"}}``; top-level keys like ``repos=[...]``.
    """
    data = read_raw(path)
    for table, values in (tables or {}).items():
        data[table] = {**(data.get(table) or {}), **values}
    data.update(top)
    config = Config.model_validate(data)  # never write a file the recorder cannot load
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".toml")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(dumps(data))
    Path(tmp).chmod(0o600)
    Path(tmp).replace(path)
    return config
