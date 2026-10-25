# ADR 0004: LLM calls run on the Mac through `cursor-agent -p`

**Status:** accepted (24 Oct 2026), to be implemented in Phase 2

**Context.** The user has a Cursor subscription and asked to use it rather than a separate API key. The phone app cannot run the CLI, and it must not hold Cursor credentials.

**Decision.** An `LLMProvider` interface with these adapters:
- `cursor_cli` (default): `cursor-agent -p --output-format json`, run in an empty sandbox directory without `--force`.
- `claude_cli`.
- `anthropic_api`.

The app queues generation jobs in Supabase, and the Mac worker runs them.

**Consequences.** Generation needs the Mac awake. Output is validated against a schema and grounded against evidence in code, never trusted as is.
