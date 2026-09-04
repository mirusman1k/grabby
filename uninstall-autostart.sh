#!/usr/bin/env bash
PLIST="$HOME/Library/LaunchAgents/com.local.grabby.plist"
launchctl unload "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
echo "Autostart removed."
