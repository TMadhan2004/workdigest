#!/usr/bin/env bash
# WorkDigest installer for macOS.
#
#   curl -fsSL https://<site>/install.sh | bash
#
# What it does, in order (it never asks for sudo; everything goes into your home folder):
#   1. checks this is a Mac (Apple Silicon or Intel);
#   2. installs uv, the Python tool manager, if you do not have it (from astral.sh);
#   3. installs the WorkDigest version named in the website's version.json;
#   4. opens `digest setup` in your browser.
#
# Developers: WORKDIGEST_SITE=<url> picks another website; WORKDIGEST_WHEEL=<path or url>
# installs a local build instead of the release; WORKDIGEST_NO_BROWSER=1 only prints the setup link.
set -euo pipefail

SITE="${WORKDIGEST_SITE:-__SITE_URL__}"
say() { printf '\033[1;35m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || fail "WorkDigest's recorder runs on macOS only."
case "$(uname -m)" in
  arm64 | x86_64) ;;
  *) fail "unsupported Mac processor: $(uname -m)" ;;
esac
command -v curl >/dev/null || fail "curl is needed."
command -v git >/dev/null || say "git is not installed yet; macOS will offer to install it the first time it is used."

# 2. uv
UV="$(command -v uv || true)"
if [ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ]; then UV="$HOME/.local/bin/uv"; fi
if [ -z "$UV" ]; then
  say "Installing uv (Python tool manager) from https://astral.sh/uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  UV="$HOME/.local/bin/uv"
fi
[ -x "$UV" ] || fail "uv was not installed; see https://docs.astral.sh/uv/"

# 3. WorkDigest
WHEEL="${WORKDIGEST_WHEEL:-}"
if [ -z "$WHEEL" ]; then
  say "Checking the latest release at $SITE"
  tmp="$(mktemp -t workdigest-version)"
  trap 'rm -f "$tmp"' EXIT
  curl -fsSL "$SITE/version.json" -o "$tmp" || fail "could not reach $SITE"
  WHEEL="$(plutil -extract mac.wheel_url raw -o - "$tmp" 2>/dev/null || true)"
  if [ -z "$WHEEL" ]; then
    WHEEL="$(sed -n 's/.*"wheel_url"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$tmp" | head -n 1)"
  fi
  [ -n "$WHEEL" ] || fail "the website's version.json has no Mac package"
fi
say "Installing WorkDigest from $WHEEL"
"$UV" tool install --force --python 3.12 "$WHEEL"
BIN="$("$UV" tool dir --bin)"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) "$UV" tool update-shell >/dev/null 2>&1 || true ;; # puts $BIN on your PATH for new terminal windows
esac
DIGEST="$BIN/digest"
[ -x "$DIGEST" ] || fail "the digest command was not installed in $BIN"

# 4. setup
say "Opening the setup page in your browser…"
"$DIGEST" setup ${WORKDIGEST_NO_BROWSER:+--no-browser}
