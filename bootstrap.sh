#!/usr/bin/env bash
# Creates the virtualenv Grabby runs from. Invoked automatically on first
# launch, and safe to re-run by hand at any time.
set -euo pipefail
PROJECT="$(cd "$(dirname "$0")" && pwd)"

die() {
  /usr/bin/osascript -e "display alert \"Grabby\" message \"$1\" as critical" >/dev/null 2>&1 || true
  echo "$1" >&2
  exit 1
}

# yt-dlp has deprecated Python 3.9, and the code uses 3.10+ syntax. macOS
# only ships 3.9, so look for a newer interpreter before falling back.
find_python() {
  local candidates=(
    /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3.13
    /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11
    /opt/homebrew/bin/python3.10 /opt/homebrew/bin/python3
    /usr/local/bin/python3.14 /usr/local/bin/python3.13
    /usr/local/bin/python3.12 /usr/local/bin/python3.11
    /usr/local/bin/python3.10 /usr/local/bin/python3
    python3.14 python3.13 python3.12 python3.11 python3.10 python3
  )
  for c in "${candidates[@]}"; do
    local bin
    bin="$(command -v "$c" 2>/dev/null)" || continue
    if "$bin" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      echo "$bin"; return 0
    fi
  done
  return 1
}

PY="$(find_python)" || die "Grabby needs Python 3.10 or newer.

macOS only includes Python 3.9. Install a current one with:

    brew install python

then open Grabby again."

echo "Using $PY ($("$PY" --version 2>&1))"
rm -rf "$PROJECT/.venv"
"$PY" -m venv "$PROJECT/.venv" || die "Could not create the virtualenv."
"$PROJECT/.venv/bin/pip" install -q --upgrade pip
"$PROJECT/.venv/bin/pip" install -q -r "$PROJECT/requirements.txt" \
  || die "Could not install dependencies. Check your internet connection."

command -v ffmpeg >/dev/null 2>&1 || [ -x /opt/homebrew/bin/ffmpeg ] || \
  /usr/bin/osascript -e 'display alert "Grabby" message "ffmpeg was not found. Downloads above 720p and mp3 extraction need it:

    brew install ffmpeg"' >/dev/null 2>&1 || true

echo "Setup complete."
