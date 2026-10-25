# WorkDigest

**Your daily digest, drafted from evidence.** WorkDigest notes what you did on your Mac today: agent sessions in Claude Code and Cursor, commits, tests, Jira and pull requests. In the evening it drafts your digest in your manager's format. You review it on your Android phone and send it yourself. Nothing is ever sent automatically.

Website and downloads: **https://workdigest.vercel.app**

## Get started (teammates)

1. **On your Mac**, open Terminal and run:
   ```sh
   curl -fsSL https://workdigest.vercel.app/install.sh | bash
   ```
   It installs WorkDigest in your home folder (no admin password) and opens a setup page in your browser. The page walks you through:
   - your `@ei.study` account;
   - your manager and your past digests (WorkDigest learns your format from them);
   - your repositories;
   - your Jira and Bitbucket tokens;
   - Cursor or Claude;
   - starting the background service.
2. **On your Android phone**, install the app from the website and sign in with the same email and password.

Two hours before the time you send your digest (you choose it during setup) the draft is ready on your phone, and on weekdays the phone reminds you an hour and a half before. To change anything later, run `digest setup` again. To pause WorkDigest, run `digest service stop`; `digest service start` starts it again. When there's a new version, the phone and the Mac tell you: install it from the app, or run `digest update` on the Mac.

## What is in this repository

| Part | Path |
|---|---|
| Mac recorder and digest engine: hooks, transcripts, git, Jira and Bitbucket, redaction, SQLite, drafting, the daemon, setup, update and uninstall | [recorder/](recorder/) |
| Supabase backend: schema, row-level security, realtime, schedules and tests | [supabase/](supabase/) |
| Android app (Flutter) | [app/](app/) |
| Website and the Mac setup wizard (Angular) | [web/](web/) |
| One-line installer | [install/install.sh](install/install.sh) |
| Design tokens shared by the app and the web | [design/tokens.json](design/tokens.json) |

## Documentation

- [Setup on macOS](docs/setup-macos.md): the one-line install, and the developer setup from source.
- [Commands](docs/commands.md): every `digest` command (install, update, start and stop the service, …).
- [Architecture](docs/architecture.md).
- [Releasing](docs/release.md): how to cut a release, and the secrets it needs.
- [Known limitations](docs/limitations.md).
- [Decisions](docs/adr/).
- [Test-case catalog](docs/test-cases.md).
- [Contributing](CONTRIBUTING.md).

## Development

`make check` runs everything CI runs:

- the public-data scan;
- Python lint, `mypy --strict` and the recorder tests with their coverage gates;
- the test-catalog check;
- the Flutter checks;
- the web builds, unit tests and Playwright (desktop and phone sizes, with axe).

`make db-test` runs the database tests against a local Supabase stack.

> **Data policy.** Redacted work summaries go to your own Supabase account, and to the AI you already use (your Cursor or Claude subscription) when drafting. Check your company's policy on code-derived data first. You can turn each kind of data off in Settings, or choose "Facts only" to send nothing to an AI.
