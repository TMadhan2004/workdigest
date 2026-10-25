# Known limitations

This page is kept honest. Each item says what is affected and what the system does about it.

## Capture (Phase 1)

| Area | Limitation | Mitigation |
|---|---|---|
| Cursor hooks | Built from cursor.com/docs (hooks and third-party-hooks pages, read 24 Oct 2026). Not yet observed live on Cursor 3.24.12 on this Mac. The `beforeSubmitPrompt` output contract (`{"continue": true}`) and the field names come from the docs only. | Hooks fail open. Transcript backfill covers missed prompts. The first live day will be checked with `digest today`. |
| Cursor transcripts | Transcript lines have no timestamps. Tool results are not stored, and tool calls have no ids. | Prompts use the `<timestamp>` tag. Tool calls are flagged `ts_approx`. Test counts for Cursor come only from the `afterShellExecution` hook. Transcript tool calls are skipped when hooks captured that conversation. |
| Cursor conversation id | The hook `conversation_id` is assumed to equal the transcript folder name. This is unverified. | If they differ, one conversation appears as two sessions. It will be checked on the first live day. |
| Claude + Cursor double hooks | With Third-Party Imports on, Cursor runs our Claude hook too. | The source is detected from the payload (`cursor_version`) and deduplicated by natural key. |
| Prompt dedupe | Prompts are deduplicated by normalized text per session. | Repeating the same prompt in one session (for example "continue") is stored once. Only the count is affected. |
| Test runs | The parsers support vitest, jest, karma, pytest, flutter/dart, playwright, mocha and go. Other runners produce no count. Go without `-v` reports packages, not tests. | The unit is stored as `tests` or `packages`. Missing counts become gaps in the digest, never guesses. |
| Test-command detection | A command counts as a test run if it contains a test keyword. A chained command (`… && npx jest`) counts. | The exact command and summary line are stored as evidence for review. |
| Branch per event | The branch is the repo's current branch at ingest time (within about 60 s for hooks; approximate for backfill). | Commits carry their real branches from git. |
| Repo attribution | Repos come from the configured repo paths, then git, then a folder-name match against configured repos (this covers moved folders). Anything else gets no repo. | Configure every repo you work in. |
| Hook latency | About 30 ms per event (Python start-up). Hooks run with `async: true`, except `SessionEnd`. | Measured in TC-REC-009. |
| Spool | Raw payloads sit unredacted in the 0600 spool until ingest. | Spool files are deleted 2 days after full ingest. Redaction runs before anything reaches SQLite. |

## Digest engine (Phase 2)

| Area | Limitation | Mitigation |
|---|---|---|
| LLM via `cursor-agent` | Verified live on 24 Oct 2026 (`cursor-agent` 2026.03.30, logged in). A draft takes about 3 minutes, almost all of it the single draft call; session summaries run 4 at a time and are cached. Each call carries about 15k tokens of agent overhead. Cursor's OS sandbox is not available on this Mac ("Sandbox mode is enabled but not available"), so read-only rests on `--mode ask`, never passing `--force`, and an empty temp workspace. | If a call fails, the draft falls back to facts only and shows a gap. `claude_cli` is the alternative provider. |
| Reply parsing | The classifier is keyword-based. It handles the four real replies well, but soft wording can be mislabelled (for example "I understand… even so, please…"). | The optional LLM pass relabels, and every item is confirmed or dismissed by you. |
| Ask merging | Repeats are matched by distinctive-word overlap, calibrated on 19–23 Oct. Unusual rewordings may not merge, and two different asks with the same nouns could. | Questions never merge with actions. The `times_raised` count is shown. Triage with `digest asks set`. |
| Imported history | Importing auto-confirms every item, so asks you already handled show as open (29 after import). | Close them once with `digest asks set <id> done --evidence …`. |
| Test suites | One line per repo and runner: the latest full-size run of the day. Commands run from a folder that is not a configured repo show as `unknown-repo`. | Configure every repo. The exact command and summary line are in the evidence pack. |
| Commit counts | All my commits authored that work day, with rebased copies collapsed (same subject and author time). Commits that were amended or squashed into one appear as separate commits if their subjects differ. | Counts link to the branch. Compare with `digest today`. |
| Named tests, atomic-commit result | Not yet captured automatically, so they always show as gaps. | Planned: parse test names from diffs, and record the atomic-commit skill output. |
| Ticket titles | Taken from Jira once synced; otherwise from digests you sent. | Unknown titles show `[title: confirm]` and a gap. |

## Jira and Bitbucket (Phase 3)

| Area | Limitation | Mitigation |
|---|---|---|
| Live verification | Verified live on 24 Oct 2026 for your account. Both tokens authenticate, and a 23 Oct sync took about 30 s: 6 tickets, 9 events, 6 PRs. The results match reality: your 20:58 comments on EIT-8403 and EIT-8383, identity-service #312 open since 5 Oct, and exactly the 5 In Progress tickets Priya listed. | Re-run `digest atlassian check` if a token expires. |
| `updatedBy()` JQL | Live Jira rejects a nested `currentUser()` call. | The account id from `/myself` is passed instead (TC-ATL-007). |
| "In Progress" | Jira's In Progress *category* includes Review and In Testing (27 tickets for you). | The rule counts only the status named "In Progress" (5 tickets, matching Priya's list). Review/Testing tickets are listed separately. |
| Verified in docs (24 Oct 2026) | A scoped token covers one app, so Jira and Bitbucket need two tokens. Jira scoped tokens must call `api.atlassian.com/ex/jira/{cloudId}`. `/rest/api/3/search` is gone (410), so `/search/jql` with `nextPageToken` is used. Bitbucket PR reads need `read:pullrequest:bitbucket`. | — |
| Jira paging | Community reports say `search/jql` can repeat `nextPageToken`. | Paging stops when a token repeats or after 20 pages. |
| Bitbucket PR activity | The exact shape of approval and update entries comes from the documentation, not yet from a live response. | Parsing is defensive. A self-approval never counts as review. |
| "Defects as a Jira Bug linked to the parent" | Not checked automatically yet. | It stays a manual real-action rule. |
| Comment text | My Jira comment excerpts (redacted, 300 characters) become evidence for the digest. | Turn this off with `[transmit]`, or keep the Jira token unset. |

## Cloud sync (Phase 4)

| Area | Limitation | Mitigation |
|---|---|---|
| What is uploaded | Only redacted data: summaries, drafts and their evidence lines, Priya's replies, asks, rules, Jira and PR metadata. Raw transcripts, prompts and diffs are never uploaded. | `[cloud] sync_*` flags turn off categories. `digest cloud push --dry-run` shows the exact rows. |
| Jobs need the Mac | The app and the 20:00 schedule can only *queue* work; the Mac's worker does it. | The worker sends a heartbeat (the `devices` table) so the app can show "Mac offline". |
| Polling, not Realtime | The worker polls every 30 s. | Realtime subscriptions can replace polling later; the job claim is already atomic. |
| Push notifications | The 20:00 nudge is written to the `notifications` table. A phone push (FCM/APNs) arrives with the app (Phase 5–6). | The app shows unread notifications when opened. |
| Conflicts | Asks, reply items and digests edited in the app win, because the Mac applies them on pull. Other tables are Mac-authoritative. | `updated_by` records who changed a row last. |
| CI | The pgTAP step needs Docker in Bitbucket Pipelines (`services: [docker]`). The Python cloud integration tests are skipped when no local stack runs. | Run `make check` and `make db-test` on the Mac before pushing. |
| Database password | It appeared in this session's transcript when `creds.txt` was read. | Reset it in Supabase, then run `digest secrets set supabase-db`. |

## Flutter app (Phase 5)

| Area | Limitation | Mitigation |
|---|---|---|
| Devices | Verified on web (release build, headless Chrome), headless `flutter test`, and the Android Pixel 9 emulator (API 36), where `integration_test/app_test.dart` passes. Not run on iOS (Xcode not installed) or a physical phone. | Install Xcode for iOS. |
| iOS share target | Android is registered for `text/plain` shares. iOS needs a Share Extension target created in Xcode. | Until then, paste the reply in the app on iOS. |
| Offline | The last digest, asks, day and timeline are cached read-only, and a banner shows when you are offline. Edits, sharing and asks need the network. | Errors are shown with retry, and nothing is lost on the Mac. |
| Live updates | Job progress is polled every 3 s, not pushed over Realtime. | It works within the jobs' typical 1–3 minutes. |
| Phone notifications | The 8 pm "Draft ready" note shows in the app (Today) and as a Mac notification, but not as a phone push notification. | See Phase 6. |
| Golden tests | Text renders in Flutter's test font (blocks), so goldens check layout, colour and spacing, not wording. | Wording is covered by widget tests. |
| Template editor | Edits titles and guidance of existing sections. It does not add or reorder sections in the app. | Edit `template.json` on the Mac for structural changes. |

## Automation (Phase 6)

| Area | Limitation | Mitigation |
|---|---|---|
| Mac asleep | launchd does not wake the Mac. If the lid is closed at 20:00, the queued job runs when it wakes, and the app shows the Mac as offline. | Keep the Mac awake in the evening, or tap "Draft now" later. |
| Phone push | There is no FCM/APNs push. Android now builds, but FCM needs a Firebase project. | The nudge shows on the Mac (Notification Centre) and on the app's Today screen. |
| One loop | Tasks run one after another in one process. The hourly summary pass (about 1–2 min with `cursor-agent`) delays the cloud tick by that much. | Jobs from the app are queued, not lost. Summaries are cached, so later passes are short. |
| Notification permission | macOS may ask once to allow notifications from `osascript`. If denied, nudges are silently missing. | Allow it in System Settings → Notifications → Script Editor. The app still shows the nudge. |
| Desktop folder access | The repos and this project live under `~/Desktop`. macOS can restrict background access to it. It worked here without a prompt (verified 24 Oct). | If `digest doctor` or `logs/` shows "Operation not permitted", give the venv's Python Full Disk Access, or move the repos out of `~/Desktop`. |
| End-to-end test | The e2e test runs the real hook script, ingest, worker and draft against an in-memory Supabase stand-in, and the app renders its output. It does not exercise real PostgREST or pg_cron. | Those are covered by the pgTAP tests (`make db-test`) and the live checks in Phase 4. |

## App v2: realtime, hidden sessions, My rules, Aurora design

| Area | Limitation | Mitigation |
|---|---|---|
| Realtime | Changes made while the app is in the background are not delivered. | On resume the app re-reads everything on screen. Job progress also re-reads every 20 s while waiting. |
| Mac offline detection | A crashed or sleeping Mac sends no goodbye, so the app shows it offline about 60 s after the last heartbeat. A normal stop shows offline at once. | The status ages on a 10 s tick without a refresh. |
| Hidden sessions | Hiding applies to the whole session on every day it spans. Session summaries stay in the cloud, marked by the hidden list, so Restore needs no re-sync. | Restore in Settings → Hidden sessions. |
| My rules | Exclusions cover PRs (by number, repo or age), tickets and repos. Everything else is an instruction the AI follows on a best-effort basis; it is not checked in code. | The app labels instruction-only rules. Confirm shows exactly what will be enforced. |
| My rules, reading | Reading a rule needs the Mac. Without an LLM, only patterns are recognised: PR numbers and ranges, "older than N days, weeks or months", ticket keys, and repo names from your config. | The rule waits as "reading" until the Mac is online. Reword it if the chips are wrong. |
| Glass | Blur is skipped under reduced motion or high contrast, and the background is static, to save battery. | Surfaces render solid with the same contrast. |

## Later phases (to verify before implementing)
- **LLM through `cursor-agent -p`.** Generation needs the Mac awake and online. The CLI is a scripting tool, not an API with an SLA. In 3 live runs (11 calls: 8 session summaries and 3 drafts), none failed. Retries after invalid JSON are not logged yet, so first-attempt reliability is unmeasured.
- **Day rating.** The rating is always asked; it is never inferred.
- **WhatsApp.** WhatsApp is never read or automated. The manager's reply arrives by paste or the share sheet.
