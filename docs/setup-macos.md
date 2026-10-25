# Setup on macOS

## For teammates: one command

```sh
curl -fsSL https://workdigest.vercel.app/install.sh | bash
```

It installs WorkDigest in your home folder (with [uv](https://docs.astral.sh/uv/); no admin password). Then it opens a setup page in your browser that only runs on your Mac (127.0.0.1). The page has ten steps, and each one checks that it worked:

1. **Welcome**: what happens and what stays on the Mac.
2. **Account**: your `@ei.study` email and a password, then the confirmation email.
3. **You and your manager**: your name and git email (suggested from `git config`), your manager's first name, **when you usually send your digest** (the draft is prepared two hours earlier, and the phone reminds you an hour and a half before, Monday to Friday), and the people you usually work with.
4. **Your digest format**: upload a `.txt` or `.md` of past digests and replies, with `Me:` before each digest you sent and `Reply:` before each reply. You see the sections, greeting, header lines and sign-off learned from it before anything is saved. Your manager's open asks and rules are imported too.
5. **Repositories**: pick a folder; the git repositories in it are found, and you tick the ones that count.
6. **Jira and Bitbucket**: scoped, read-only tokens (the exact scopes are on the page and in section 6 below). They are stored in the Keychain only and tested at once.
7. **AI for drafts**: Cursor and Claude Code are each tried with a tiny prompt. Choose a working one, or "Facts only". If one says *signed out*, run its login command in Terminal (`cursor-agent login`, or `claude` then `/login`) and press *Check again*.
8. **Agent capture**: installs the Claude Code and Cursor hooks you agree to. Each settings file is backed up first.
9. **Start**: turns on the background service and waits for this Mac's first heartbeat.
10. **Phone**: a QR code for the Android app. Sign in with the same email and password.

| Command | What |
|---|---|
| `digest setup` | Opens the page again to change anything; steps you already did are remembered. |
| `digest update` | Installs the newest release and restarts the service. The Mac and the app tell you when there is one. |
| `digest uninstall` | Removes the service, the hooks and the Keychain items. It asks before deleting your data. Then run `uv tool uninstall workdigest`. |
| `digest service stop` / `digest service start` | Stops the background service (it stays stopped after a restart) or starts it again. `digest service status` shows whether it runs. |

Every command is listed in [commands.md](commands.md).

> **Keychain prompt.** If macOS asks whether "python3.12" may use confidential information stored in "WorkDigest", and *you* just installed or updated WorkDigest, choose **Always Allow**. Keychain items belong to the program that created them, so a newly installed copy asks once. If you didn't expect it, choose **Deny**.

The rest of this page is the **developer setup from source**, as used to build WorkDigest. It is not needed after the one-line install.

---

# Developer setup from source

Tested on macOS 26.6 (Apple silicon), Python 3.14, uv 0.10, Claude Code 2.1.x and Cursor 3.24.

## 1. Install

```sh
brew install uv            # if missing
cd work_tracker
make setup                 # creates recorder/.venv with all dependencies
alias digest='uv --directory "$PWD/recorder" run digest'
```

## 2. Configure

```sh
digest init --name "Arjun K" --email arjun.k@ei.study
open "$HOME/Library/Application Support/WorkDigest/config.toml"
```

Add one `[[repos]]` block per repository to watch:

```toml
[[repos]]
path = "~/Desktop/portal/portal-service-v4"

[[repos]]
path = "~/Desktop/mcp/api-gateway"
```

Agent sessions in folders that aren't reportable work, such as this tool's own repo, can be left out entirely. Hooks and transcripts from these folders are skipped and never stored:

```toml
[capture]
exclude_paths = ["~/Desktop/archive/work_tracker"]
```

Run `digest doctor` to validate the config.

## 3. Install the hooks

Each settings file is backed up next to itself (`*.workdigest-backup-<time>`) before it is changed. Only WorkDigest's own entries are added or removed.

```sh
digest hooks install       # ~/.claude/settings.json and ~/.cursor/hooks.json
digest hooks status
```

- Restart Cursor after installing.
- Claude Code reads the new hooks at the next session start.
- To remove the hooks: `digest hooks uninstall`.

> Cursor also runs Claude Code hooks by default (Settings → Agents → Third-Party Imports). WorkDigest detects this and deduplicates, so you can leave that setting as it is.

## 4. Use

```sh
digest today                   # ingest, then print today's evidence
digest today --date 2026-10-23 --json
digest ingest                  # one ingest pass
digest run                     # foreground loop (Phase 6 installs it as a launchd agent)
```

## 5. Daily digest (Phase 2)

First, once only, import your previous digests and Priya's replies. This seeds the five-day plan, the open asks and the standing rules:

```sh
digest history import docs/daily-digest-and-reply.txt
digest asks                    # triage: digest asks set <id> done --evidence <link>
digest rules
```

Then each evening:

```sh
digest reply reply.txt --yes   # Priya's reply to yesterday (or `-` and paste)
digest close --rating 7 --team "Rahul, Vikram"
digest draft                   # WhatsApp text + the gap panel; nothing is sent
digest section risks           # regenerate one section only
digest draft --show-input      # exactly what would be sent to the LLM
digest check my-digest.txt     # run the standing rules on any digest text
```

**LLM.** The default provider is `cursor_cli`, which uses your Cursor subscription. Log in once with `cursor-agent login`. It runs read-only (`--mode ask`, sandbox enabled) in an empty temp folder. You can switch providers in `config.toml` under `[digest] provider` (`claude_cli` or `none`). Use `[transmit]` to control what is sent, or `llm_enabled = false` to send nothing. With `none` you still get every fact (header, tests, commits, branch links), and the narrative sections are left as placeholders.

## 6. Jira and Bitbucket (Phase 3, read-only)

Create two scoped API tokens at <https://id.atlassian.com/manage-profile/security/api-tokens>. Each token is tied to one app:

| Token | App | Scopes |
|---|---|---|
| Jira | Jira | `read:jira-work`, `read:jira-user` |
| Bitbucket | Bitbucket | `read:repository:bitbucket`, `read:pullrequest:bitbucket`, `read:user:bitbucket` |

Store them in the Keychain. The input is hidden, and the tokens are never written to files or logs:

```sh
digest secrets set jira
digest secrets set bitbucket
digest atlassian check          # who each token belongs to
digest atlassian sync --date 2026-10-26
```

`digest draft` runs the sync automatically. It covers tickets you touched (title, status, due date, parent), your comments and status changes, tickets In Progress, keys that don't open, and your PRs with opened → approved/declined times.

## 7. Cloud sync (Phase 4, Supabase)

The schema lives in `supabase/migrations/`. It's applied with `supabase db push --linked`, and its tests run with `make db-test`. Every table has row-level security, so you only ever see your own rows. The Mac uses only the public publishable key plus your own sign-in; the service-role key is never used.

```sh
digest cloud setup your-project-ref       # project URL + publishable key into config.toml (the wizard does this for teammates)
digest cloud signup --email arjun.k@ei.study # one-time; confirm the email Supabase sends
digest cloud login                            # refresh token goes to the Keychain
digest cloud push --dry-run                   # exactly what would be uploaded
digest cloud push
digest cloud worker                           # foreground worker (the daemon in section 9 replaces this)
```

Two hours before your send-by time (`nudge_time`, set in setup or in the app), a schedule in Supabase queues the draft. The Mac picks it up, generates the draft and posts "Draft ready, N gaps to confirm" to the app and to the Mac's Notification Centre.

## 8. The app (Phase 5, Flutter)

The app is built with the public Supabase settings; no secret ships in the app:

```sh
cd app
KEY=$(grep publishable_key "$HOME/Library/Application Support/WorkDigest/config.toml" | cut -d'"' -f2)
flutter run -d chrome \
  --dart-define=SUPABASE_URL=https://your-project-ref.supabase.co \
  --dart-define=SUPABASE_PUBLISHABLE_KEY=$KEY
```

Sign in with the same account as `digest cloud login`. The background daemon (section 9) does the work the app requests: drafts, regenerating a section, reading a reply. Add `--dart-define=SITE_URL=https://workdigest.vercel.app` to try the update checker; release builds get all three values from the release workflow.

| Platform | Status on this Mac |
|---|---|
| Web (Chrome) | Builds and runs (`flutter build web`) |
| Android | Builds and runs on the Pixel 9 emulator (API 36); `integration_test/app_test.dart` passes there. Run it with `flutter run -d emulator-5554` plus the two `--dart-define`s above. The WhatsApp share target is registered in `AndroidManifest.xml`. |
| iOS | Needs full Xcode. The iOS share extension still has to be added in Xcode. |

`receive_sharing_intent` is pinned to 1.8.1. Version 1.9 needs Android Gradle Plugin 9 with SDK 37 and ships only as a Swift package, which made `flutter pub get` fail.

Tests read fixtures from `test/fixtures/embedded_fixtures.dart`, generated by `scripts/embed_app_fixtures.py`, so they also run on a device. `make app-check` fails if that file is stale.

`make app-check` runs the analyzer (zero issues allowed), all app tests and the 85% coverage gate. Update the golden screenshots after an intended visual change with `flutter test --update-goldens test/golden`.

## 9. Run everything in the background (Phase 6)

```sh
digest service start       # per-user launchd agent: starts now and at login, restarted if it exits
digest service status      # installed / loaded / pid
digest service restart     # restart it (config.toml changes apply on the next pass anyway)
digest service stop        # stop it and remove ~/Library/LaunchAgents/com.workdigest.daemon.plist
digest daemon --once       # run every task once in the foreground (for checking)
```

The agent runs `digest daemon`, one process that does all the recurring work:

| Task | Every | |
|---|---|---|
| Hook spool → SQLite | 1 min | |
| Transcripts and git | 5 min | |
| Cloud worker | 30 s | heartbeat, app edits, jobs from the app and the scheduled draft, push |
| Jira and Bitbucket | 30 min | your PRs (open, approvals) and Jira activity; skipped without tokens |
| Session summaries | 1 hour | cached; unchanged sessions cost no LLM call |
| Draft | at `digest.nudge_time` (two hours before `send_deadline`) | only without cloud sync; with cloud the Supabase schedule queues it |
| Send reminder | 15 min before `digest.send_deadline` | once a day, only if the digest isn't marked sent in the app |

Notifications ("Draft ready – send by 18:00, N gaps to confirm" and "Send before 18:00", with your own time) appear in macOS Notification Centre. The phone also reminds you on weekdays, an hour and a half before your send-by time. The first one may ask to allow notifications for Script Editor/osascript. Logs: `logs/daemon.log` (rotated, 1 MB × 3) and `logs/launchd.err.log`. `digest doctor` shows whether the daemon is running.

Stop any `digest cloud worker` you started by hand before starting the service, so only one process works the queue. A second worker is safe (jobs are claimed atomically), just wasteful.

## 10. Using the app (v2)

- **Live updates.** The Mac status ("Mac connected · writing your draft…"), job progress, drafts, asks, rules and the Timeline update by themselves over Supabase Realtime. The small pill on Today shows the connection: Live, Reconnecting or Offline. When the app comes back to the foreground it re-reads everything.
- **Hide a session** (e.g. personal work). In the Timeline, swipe a session left or use its ⋯ menu. It is left out of every digest from the next draft, and its data stays on the Mac. Restore it in Settings → Hidden sessions. If a day's draft was written before you hid something, the Timeline offers a **Redraft**.
- **My rules.** Asks → Rules → type a rule in your own words, e.g. "Don't include quiz-service PRs 3–7" or "Hide PRs open more than 30 days". The Mac reads it (Cursor, or patterns without an LLM) and shows **Understood as** chips. Nothing applies until you tap **Confirm**.
  - PR, ticket, repo and age exclusions are enforced in code.
  - Anything else becomes an instruction for the AI and is marked "best effort".
  - Each rule can be switched off, edited or deleted. "Apply to today's draft" redrafts now.
- **Priya's replies, asks and rules.** Add his reply and confirm what the Mac read. Standing rules join *Priya's rules*; actions and questions become **open asks**. Repeated asks are merged ("raised 3×"), and the "needs a real action", ticket and date labels are kept.
  - Each open ask has two choices. **Done** needs evidence (a link, ticket or commit). **Disagree** needs a reason.
  - The next digest's **closing reports every ask you answered since the previous digest**, once each: *"<ask>: done – <evidence>."* or *"<ask>: I disagree – <reason>."* The closing is written by code, not the AI.
  - **Open asks are never mentioned in the digest.** Asks that need a real action stay on your gap list in the app as a reminder.
  - **Delete one** with an open ask's ⋯ menu or by swiping it left, and one of Priya's rules with its delete button. Each delete offers **Undo**.
  - **Clear all** on the Open tab clears every open ask. Cleared asks are never reported, and if Priya asks again it becomes a new ask. **Clear all** under *Priya's rules* switches his rules off; rules in his future replies are added again.
  - The **Closed** tab filters by the day asks were closed: chips for recent days, or **Pick a day**.
- **Open PRs** (Timeline) are your PRs from Bitbucket. The Mac refreshes them every 30 minutes and on every draft, and the list updates live. PRs that your confirmed rules exclude are folded under **Hidden by your rules**, exactly as they are left out of the digest.
- **History** shows a month calendar. Each day's dot shows its digest: sent (green), edited (violet) or draft (amber). Tap a day to open it, and swipe or use the arrows to change month.
- **Your digest time.** Settings → Your digest time → **Send your digest by**. The draft is prepared two hours earlier, and **Remind me on this phone** sends a reminder an hour and a half before, Monday to Friday (a day you marked sent is skipped).
- **Digest template.** Settings → Digest template: tap a section to edit its title and guidance, use its ⋯ menu to delete it (with Undo), or **Add section** at the end. New sections are written by the AI from your guidance.
- **Notifications (the bell on Today).** Unread notices from your Mac. The newest also shows as a card on Today. Each one opens where you act on it:
  - **Draft ready**: opens that day's draft.
  - **AI stopped working / works again**: opens Settings, where the AI choice and each provider's state are.
  - **Mac update available**: shows `digest update` to copy and run in Terminal.

  **Mark all as read** clears the dot. Weekday "Time to send your digest" reminders and "a new app version is live" are ordinary Android notifications, not in the bell.
- **Appearance.** Settings → Appearance: Dark (default), Light or System.

## Where data lives

All data lives under `~/Library/Application Support/WorkDigest/`, owner-only (0700 for directories, 0600 for files):

- `spool/`: raw hook payloads, deleted 2 days after ingest.
- `workdigest.sqlite3`: redacted evidence.
- `config.toml`.
- `logs/`: daemon and launchd logs.

Redacted summaries, drafts, asks and rules are synced to your own Supabase account (row-level security), and the AI you choose receives the redacted evidence when drafting. Raw prompts, code and tool output never leave the Mac.
