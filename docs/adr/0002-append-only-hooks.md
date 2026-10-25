# ADR 0002: Hooks append to a spool; the daemon does everything else

**Status:** accepted (24 Oct 2026)

**Context.** Hooks run inside live agent sessions. Any latency, crash or stdout output can disturb parallel work or inject text into Claude's context.

**Decision.** The hook reads stdin and appends one JSON line under `flock`. It always exits 0 and prints nothing, except the `{"continue": true}` that Cursor's `beforeSubmitPrompt` requires. Redaction, normalization and storage happen in the daemon.

**Consequences.** Raw payloads exist briefly in a 0600 spool, which is deleted 2 days after ingest. Ingest has a delay of up to one minute.
