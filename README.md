# WorkDigest

Builds a draft of my daily WhatsApp digest for Priya from evidence captured passively during the day: agent sessions, commits, tests and tickets. I review the draft, edit it and send it myself. Nothing is ever sent automatically.

| Part | Path | Status |
|---|---|---|
| Mac recorder (hooks, transcripts, git, redaction, SQLite, CLI) | [recorder/](recorder/) | Phase 1 |
| Digest engine (summaries, template, rules and asks, compliance, WhatsApp text) | [recorder/src/workdigest/digest/](recorder/src/workdigest/digest/) | Phase 2 |
| Jira and Bitbucket Cloud (read-only) | `recorder/src/workdigest/integrations/` | Phase 3 |
| Supabase backend | [supabase/](supabase/) | Phase 4 |
| Flutter app | [app/](app/) | Phase 5 |

- Setup: [docs/setup-macos.md](docs/setup-macos.md)
- Architecture: [docs/architecture.md](docs/architecture.md)
- Known limitations: [docs/limitations.md](docs/limitations.md)
- Test-case catalog: [docs/test-cases.md](docs/test-cases.md)
- Decisions: [docs/adr/](docs/adr/)

`make check` runs Python lint, `mypy --strict`, the tests with coverage gates, the test-catalog check, and the Flutter app checks. `make db-test` runs the database tests.

> ⚠️ **Data policy.** Later phases send redacted work summaries to Supabase and to an LLM through the Cursor CLI. Check your company's policy on sending code-derived data to cloud and LLM services first. Phase 1 keeps everything on the Mac.
