# Contributing

## Before you start

- **No real data in the repository.** It's public. Use the made-up examples: the manager Priya, Arjun K, `DEMO-101`, `billing-service`, `portal-service`. `make public-check` fails on known real names, internal repositories and project IDs; CI runs it on every push.
- **No secrets anywhere.** Tokens live only in the macOS Keychain or GitHub secrets. gitleaks runs in CI. The redaction tests' made-up secrets are allowed by exact value in `.gitleaks.toml`.
- **Nothing is ever sent for the user.** No feature may message a manager or anyone else.

## Setup

```sh
brew install uv node flutter        # Flutter 3.41, Node 20
make setup                          # recorder virtualenv
cd web && npm ci && cd ..
make check                          # everything CI runs
```

`make help` lists every target. The developer setup of the recorder itself is in [docs/setup-macos.md](docs/setup-macos.md).

## Conventions

- **Tests first.** Every test has a docstring (Python) or a comment (Dart) with a `TC-…` id, and Given / When / Then. `make catalog` regenerates [docs/test-cases.md](docs/test-cases.md), and CI fails if it is stale.
- **Design tokens.** Colours, spacing, radii and motion live in `design/tokens.json`. Run `make tokens` after changing them; the CSS and Dart files are generated.
- **Database changes** are new files in `supabase/migrations/` with pgTAP tests in `supabase/tests/`. Never edit an applied migration.
- **Decisions** that change the architecture get an ADR in `docs/adr/`.
- **Commits** are small and say why. Don't add tool attribution trailers.

## Pull requests

CI must be green: recorder, database, app, web and the secret scan. Releases are cut separately; see [docs/release.md](docs/release.md).
