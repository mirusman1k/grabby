#!/usr/bin/env bash
# Runs the downloader quietly in the background, starting at every login,
# so http://127.0.0.1:8000 just always works. Undo with ./uninstall-autostart.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.local.grabby.plist"

[ -d "$DIR/.venv" ] || { echo "Run ./run.sh once first to set up."; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.local.grabby</string>
  <key>ProgramArguments</key>
  <array>
    <string>$DIR/.venv/bin/python</string>
    <string>-m</string><string>uvicorn</string>
    <string>backend.app:app</string>
    <string>--host</string><string>127.0.0.1</string>
    <string>--port</string><string>8000</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/server.log</string>
  <key>StandardErrorPath</key><string>$DIR/server.log</string>
</dict>
</plist>
PLISTEOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Installed. http://127.0.0.1:8000 will now be available after every login."
