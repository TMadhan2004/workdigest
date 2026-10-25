# ADR 0009: A team product: one shared project, Mac-first onboarding, releases from GitHub

**Status:** accepted (25 Oct 2026)

**Context.** WorkDigest worked for one engineer with one manager and one digest format. Teammates have their own managers, their own formats, Cursor or Claude (or neither), and no patience for a six-step manual setup. The code had to become public without exposing real people, tickets or internal repositories.

**Decision.**
- **One shared Supabase project.** Row-level security already isolates every row by `user_id`. Sign-ups are limited to `@ei.study` by a trigger on `auth.users`, so the database itself refuses other domains. The app and the wizard check this too, to give a friendly message.
- **Accounts are created on the Mac.** `install.sh` installs the tool with `uv` (no sudo) and runs `digest setup`: a local wizard on `127.0.0.1` with a one-time token, a Host check and a strict CSP. The phone never signs anyone up. An account with no Mac heartbeat yet sees "Finish setup on your Mac", which turns into Today when the first heartbeat arrives.
- **Each person's format is learned, not configured.** The uploaded conversation (`Me:` / `Reply:`) gives:
  - the sections, their order and which ones are one-liners;
  - the greeting, with the manager's name as a placeholder;
  - the header lines and the sign-off.

  Known sections keep the built-in code paths. Unknown sections become custom sections the LLM writes, with a style example. New users get checks derived from their own format, not the original manager's rules.
- **AI capability is probed, not assumed.** A tiny real prompt each hour tells working from signed-out, missing or over-quota. The app locks providers that don't work on that Mac. When the chosen one stops working, drafts are facts only, and the Mac and the phone are told once, then again when it recovers.
- **Releases from GitHub Actions, by hand.** One workflow builds the signed APK (refusing debug signing) and the wheel with the team settings, publishes a GitHub Release and `version.json`, and deploys the website to Vercel. Downloads redirect to `releases/latest`. Updates are checked without Firebase: on open, on resume and every 6 hours through WorkManager, with one local notification per release and a SHA-256 check before installing. `min_build` can force an update.
- **A fresh public history.** The public repository starts from one commit of the scrubbed tree. The full history stays on local `private-*` branches, and a pre-push hook refuses to push anything rooted in it. `scripts/check_public.py` stores only SHA-256 prefixes of forbidden terms, and CI runs it with gitleaks.
- **One design source.** `design/tokens.json` generates both the website's CSS and the app's Dart constants, so the two can't drift.

**Consequences.**
- Every teammate needs a Mac. Android is the only phone platform.
- The website is static: it has no server, and the update data is a file written at release time.
- A lost signing key would force everyone to reinstall, so the key is backed up outside the repository.
- Keychain items belong to the binary that created them. A move to a different Python, such as from a source checkout to the installed tool, asks the user once to allow access.
