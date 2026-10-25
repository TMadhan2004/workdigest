# Releasing WorkDigest

A release ships the Android app, the Mac package and the website together. It is cut by hand from GitHub, always from `main`.

## Cut a release

1. Make sure CI on `main` is green.
2. GitHub → **Actions** → **Release** → **Run workflow**, and fill in:
   - **version**: e.g. `0.2.0`, higher than the last one, in the form `X.Y.Z`;
   - **notes**: what's new. The first line becomes the phone notification ("WorkDigest 0.2.0 is live · …");
   - **min_build**: leave `0`. Set it only when older apps must stop working (see below).
3. Wait about 20 minutes. The workflow (`.github/workflows/release.yml`):
   1. checks the request: on `main`, a well-formed version, and the tag doesn't exist yet;
   2. runs the full CI;
   3. builds the **signed** APK. It fails if the APK would be signed with the debug key;
   4. builds the Mac wheel, with the setup wizard and the team settings inside;
   5. creates the GitHub Release `vX.Y.Z` with `WorkDigest.apk`, the wheel, `install.sh` and `version.json`;
   6. builds the website with that `version.json` and deploys it to Vercel production.

Or from a terminal:

```sh
gh workflow run release.yml -R TMadhan2004/workdigest --ref main \
  -f version=0.2.0 -f min_build=0 -f notes="What's new…"
```

## What users see

| Who | When | What |
|---|---|---|
| Phones | on opening the app, on returning to it, and every 6 hours in the background | One notification per release, plus an **Update** banner on Today and Profile. Update downloads the APK, checks its SHA-256 and opens Android's installer. |
| Macs | once a day | One Mac notification and one in-app notice: "run `digest update`". |
| New installs | at once | `/download/android` and `install.sh` always serve the newest release. |

The Android build number is `100 + the release workflow's run number`, so it always increases.

## Forcing an update

Set **min_build** to the build number of the oldest app that may keep working; the build numbers are in each release's `version.json`. Older apps then show only "Update required" until they update. Use it only for breaking changes, such as a database migration the old app can't handle.

## Rolling back

Never reuse or move a tag. Release a new version (e.g. `0.2.1`) built from a commit that reverts the change. Phones and Macs only ever move forward to a higher build.

## Secrets and variables

Repository **variables** (Settings → Secrets and variables → Actions → Variables):

| Name | Value |
|---|---|
| `SUPABASE_URL` | `https://<project-ref>.supabase.co` |
| `SUPABASE_PUBLISHABLE_KEY` | the public publishable key (row-level security protects the data) |
| `SITE_URL` | `https://workdigest.vercel.app` |

Repository **secrets**:

| Name | What |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | `base64 -i ~/.workdigest-release/release.keystore` |
| `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_PASSWORD` | from `~/.workdigest-release/keystore-password.txt` |
| `ANDROID_KEY_ALIAS` | `workdigest` |
| `VERCEL_TOKEN` | a Vercel access token scoped to the team |
| `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID` | from `.vercel/project.json` after `vercel link` |

Set them with `gh secret set NAME -R TMadhan2004/workdigest`, which asks for the value with hidden input, or pipe in a file. Never paste them into chats or commits.

> **The signing key cannot be replaced.** Android only installs an update signed with the same key as the installed app. Keep `~/.workdigest-release/` backed up in a password manager. If it is lost, every user has to uninstall and reinstall.

## Building locally

```sh
make screens                                 # real app screens (demo data) for the website
make wheel                                   # the Mac package in recorder/dist/
make site                                    # the website in web/dist/site/browser/
cd app && flutter build apk --release        # debug-signed unless the keystore env vars are set
```

To try a local Mac package without touching your real setup:

```sh
WORKDIGEST_HOME=/tmp/wd-trial WORKDIGEST_KEYCHAIN_SERVICE=WorkDigestTrial \
UV_TOOL_DIR=/tmp/wd-tools UV_TOOL_BIN_DIR=/tmp/wd-bin \
WORKDIGEST_WHEEL=$PWD/recorder/dist/workdigest-0.1.0-py3-none-any.whl WORKDIGEST_NO_BROWSER=1 \
bash install/install.sh
```

Don't press **Start** in a trial. The launchd service name is shared, so starting it would replace your real service.
