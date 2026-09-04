#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
URL="http://127.0.0.1:$PORT"

# Already running? Just open the tab and stop -- no second server, no port clash.
if curl -s -o /dev/null --max-time 1 "$URL"; then
  echo "Already running. Opening $URL"
  open "$URL" 2>/dev/null || true
  exit 0
fi

if [ ! -d .venv ]; then
  echo "First run: setting up (takes a minute)…"
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
fi

# Open the browser once the server answers, without blocking the server itself.
( for _ in $(seq 1 30); do
    curl -s -o /dev/null --max-time 1 "$URL" && { open "$URL" 2>/dev/null; break; }
    sleep 0.5
  done ) &

echo "Serving at $URL  —  press Ctrl-C to stop"
exec ./.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port "$PORT"
