# ADR 0003: Natural-key event ids shared by all capture paths

**Status:** accepted (24 Oct 2026)

**Context.** The same fact can arrive up to three times: from a Claude hook, from Cursor running that Claude hook (Third-Party Imports), and from transcript backfill.

**Decision.** `event_id = sha256(source, session_key, kind, natural_key)`. The natural key is the `tool_use_id`, the normalized prompt text, the generation id plus content, the commit SHA, or the repo plus branch. The source is detected from the payload, not the hook's argv.

**Consequences.** Identical prompts repeated in one session are stored once. Cursor transcript tool calls have no id, so they are skipped once hooks are present.
