#!/usr/bin/env bash
# Move WorkDigest's Keychain items from the source checkout's Python to the installed WorkDigest,
# so the installed version can read them without macOS asking every time.
#
# 1. The old Python (which created the items) reads them; nothing is printed.
# 2. It deletes them.
# 3. The installed WorkDigest's Python saves them again and reads them back.
#    If that fails, the old Python puts them back, so nothing is lost.
set -euo pipefail
cd "$(dirname "$0")/.."
OLD=".venv/bin/python"
NEW="$HOME/.local/share/uv/tools/workdigest/bin/python"
[ -x "$OLD" ] || { echo "Source checkout Python not found: $OLD"; exit 1; }
[ -x "$NEW" ] || { echo "Installed WorkDigest not found; run the install command first"; exit 1; }

SECRETS="$("$OLD" -c '
import json, keyring
names = ["supabase-refresh-token", "jira-token", "bitbucket-token", "supabase-db-password"]
print(json.dumps({n: v for n in names if (v := keyring.get_password("WorkDigest", n))}))
')"
echo "Read $(printf '%s' "$SECRETS" | "$OLD" -c 'import json,sys;print(len(json.load(sys.stdin)))') items (values are not shown)."

printf '%s' "$SECRETS" | "$OLD" -c '
import json, sys, keyring
for name in json.load(sys.stdin):
    keyring.delete_password("WorkDigest", name)
'

if printf '%s' "$SECRETS" | "$NEW" -c '
import json, sys, keyring
data = json.load(sys.stdin)
for name, value in data.items():
    keyring.set_password("WorkDigest", name, value)
sys.exit(0 if all(keyring.get_password("WorkDigest", n) == v for n, v in data.items()) else 1)
'; then
  echo "Done: the installed WorkDigest now owns its Keychain items."
else
  printf '%s' "$SECRETS" | "$OLD" -c '
import json, sys, keyring
for name, value in json.load(sys.stdin).items():
    keyring.set_password("WorkDigest", name, value)
'
  echo "Could not save with the installed WorkDigest; the items were put back as they were."
fi
unset SECRETS
