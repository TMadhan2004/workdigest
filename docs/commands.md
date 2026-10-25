# Commands

Everything WorkDigest does on the Mac goes through the `digest` command. After the one-line install, open a **new** Terminal window so `digest` is found. In a source checkout, run the same commands as `uv run digest …` inside `recorder/`.

`digest --help` lists everything, and `digest <command> --help` explains one command.

## Install, update and remove

| Command | What it does |
|---|---|
| `curl -fsSL https://workdigest.vercel.app/install.sh \| bash` | Installs WorkDigest, or reinstalls the latest release, then opens the setup page. |
| `digest setup` | Reopens the setup page to change anything: account, manager, format, repositories, tokens, AI, hooks, service. |
| `digest update --check` | Only says whether a newer version exists. |
| `digest update` | Installs the newest version and restarts the background service. |
| `digest uninstall` | Removes the service, the hooks and the Keychain items. It asks before deleting your data (`--delete-data` skips the question). |
| `uv tool uninstall workdigest` | After `digest uninstall`, removes the program itself. |

## The background service

The service captures your work, keeps the phone in sync and drafts the digest in the evening. It is a per-user launchd agent: no admin rights, and it starts at every login.

| Command | What it does |
|---|---|
| `digest service start` | Starts the service now and at every login. If it is already running, it is restarted. |
| `digest service stop` | Stops the service. It stays stopped, also after a restart of the Mac, until you start it again. |
| `digest service restart` | Restarts the service. Changes to `config.toml` apply on the next pass even without it. |
| `digest service status` | Shows whether it is installed and running, with its process id and the last exit code. |
| `digest daemon --once` | Runs every task once in this window, to check that things work. |
| `digest daemon` | Runs the service in this window, which is what launchd runs. |

Stopping only pauses capture and syncing; your data stays. While stopped, the app shows "Mac offline", and drafts wait until it runs again. The older names `digest service install` and `digest service uninstall` still work, as `start` and `stop`.

## Checking that things work

| Command | What it does |
|---|---|
| `digest doctor` | Checks the config, hooks and repositories, whether the service runs, and recent errors. |
| `digest hooks status` | Shows whether the Claude Code and Cursor hooks are installed. |
| `digest hooks install` / `digest hooks uninstall` | Adds or removes only WorkDigest's hooks. Each settings file is backed up first. |
| `digest atlassian check` | Shows who your Jira and Bitbucket tokens belong to, or why they fail. |
| `digest atlassian sync --date 2026-10-26` | Reads a day's Jira activity and your PRs now. |
| `digest secrets set jira` / `bitbucket` | Stores a token in the Keychain (hidden input). |
| `digest secrets status` | Shows which tokens are stored, never their values. |
| `digest cloud login` | Signs this Mac in again, for example when the app shows the Mac offline while it is on. |
| `digest cloud push --dry-run` | Shows exactly what would be uploaded, and sends nothing. |
| `digest today` | Lists everything captured today (`--date`, `--json`). |

The logs are in `~/Library/Application Support/WorkDigest/logs/` (`daemon.log`, `launchd.err.log`).

## The digest

The phone app does all of this too. These commands are the same actions from the Terminal.

| Command | What it does |
|---|---|
| `digest draft` | Writes today's draft now, with the gap list. Nothing is ever sent. `--show-input` prints exactly what goes to the AI. |
| `digest section risks` | Rewrites one section and leaves the rest as it is. |
| `digest close --rating 7 --team "Rahul, Vikram"` | Closes the day: rating, team and project for the header. |
| `digest reply reply.txt --yes` | Adds your manager's reply from a file (`-` to paste) and confirms what was read. |
| `digest asks` | Lists open asks. |
| `digest asks set <id> done --evidence <link>` | Closes an ask: `done` with evidence, or `disagree --reason …`. |
| `digest rules` | Lists your manager's standing rules. |
| `digest check my-digest.txt` | Runs the rules on any digest text. |
| `digest history import file.txt` | Imports past digests and replies (`Me:` / `Reply:` format). Setup does this from your uploaded file. |

## For developers

| Command | What it does |
|---|---|
| `make check` | Everything CI runs. |
| `make wheel` | Builds the Mac package with the setup wizard inside. |
| `make site` | Builds the website. |
| `make tokens` | Regenerates the design tokens for the web and the app. |
| `make db-test` | Runs the database tests against a local Supabase stack. |

Releases are described in [release.md](release.md).
