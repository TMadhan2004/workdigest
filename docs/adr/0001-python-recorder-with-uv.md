# ADR 0001: Recorder in Python, managed by uv

**Status:** accepted (24 Oct 2026)

**Context.** The recorder needs SQLite, Keychain access, subprocess git, launchd friendliness and fast hook start-up. Node was the alternative.

**Decision.** Python 3.12+ with uv, pydantic v2 at boundaries, Typer for the CLI, `mypy --strict` and ruff.
- `sqlite3` and `fcntl` are in the standard library.
- `keyring` gives Keychain access.
- The hook script uses only the standard library, so it runs under any `python3` in about 30 ms.

**Consequences.** Hook commands use an absolute interpreter path chosen at install time. If that interpreter is removed, re-run `digest hooks install`.
