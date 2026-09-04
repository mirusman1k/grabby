#!/usr/bin/env bash
# Builds "Video Downloader.app" and installs it to /Applications.
# Re-run this if you ever move the project folder.
set -euo pipefail
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="Grabby"
BUILD="$PROJECT/build/$APP_NAME.app"

rm -rf "$BUILD"
mkdir -p "$BUILD/Contents/MacOS" "$BUILD/Contents/Resources"

cat > "$BUILD/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundleDisplayName</key><string>$APP_NAME</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIdentifier</key><string>com.local.grabby</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
</dict>
</plist>
PLIST

clang -O2 -Wall -o "$BUILD/Contents/MacOS/launcher" "$PROJECT/build/launcher.c" \
  -DPROJECT_DIR="\"$PROJECT\"" \
  -DPYTHON_BIN="\"$PROJECT/.venv/bin/python\"" \
  -DSCRIPT="\"$PROJECT/desktop.py\""
chmod +x "$BUILD/Contents/MacOS/launcher"

cp "$PROJECT/build/icon.icns" "$BUILD/Contents/Resources/icon.icns"

# Unsigned bundles get quarantined; strip it so it opens without a Gatekeeper prompt.
xattr -cr "$BUILD" 2>/dev/null || true
codesign --force --deep --sign - "$BUILD" 2>/dev/null || echo "(ad-hoc signing skipped)"

DEST="/Applications"
[ -w "$DEST" ] || DEST="$HOME/Applications"
mkdir -p "$DEST"
rm -rf "$DEST/$APP_NAME.app"
cp -R "$BUILD" "$DEST/"
echo "Installed to $DEST/$APP_NAME.app"
