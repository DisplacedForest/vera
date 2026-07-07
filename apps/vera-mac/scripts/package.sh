#!/usr/bin/env bash
# Build, bundle, and ad-hoc-sign Vera.app from the SwiftPM executable.
# Output: apps/vera-mac/build/Vera.app
set -euo pipefail

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_ROOT"

NAME="Vera"
BUNDLE_ID="app.vera.mac"
LOGO="Sources/Vera/Resources/vera-icon.png"
BUILD_DIR="build"
APP="$BUILD_DIR/$NAME.app"
# Single repo-wide version: prefer the root VERSION file, fall back to the app-local one.
VERSION="$(cat "$APP_ROOT/../../VERSION" 2>/dev/null || cat VERSION 2>/dev/null || echo 0.0.0)"
BUILD_NUM="$(git rev-parse --short HEAD 2>/dev/null || echo 0)"

echo "==> Vera $VERSION ($BUILD_NUM)"

# 1. Icon: vera-icon.png -> Vera.icns
echo "==> Icon"
ICONSET="$BUILD_DIR/Vera.iconset"
rm -rf "$ICONSET" && mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size"       "$LOGO" --out "$ICONSET/icon_${size}x${size}.png"      >/dev/null
  sips -z $((size*2)) $((size*2)) "$LOGO" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$BUILD_DIR/Vera.icns"

# 2. Release build
echo "==> swift build -c release"
swift build -c release

BIN="$(swift build -c release --show-bin-path)"

# 3. Assemble bundle
echo "==> Assemble $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN/$NAME" "$APP/Contents/MacOS/$NAME"
cp "$BUILD_DIR/Vera.icns" "$APP/Contents/Resources/Vera.icns"
# SwiftPM resource bundles (Vera_Vera.bundle = logo, Starscream_Starscream.bundle = ws privacy manifest)
for b in "$BIN"/*.bundle; do [ -e "$b" ] && cp -R "$b" "$APP/Contents/Resources/"; done

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$NAME</string>
  <key>CFBundleDisplayName</key><string>$NAME</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleExecutable</key><string>$NAME</string>
  <key>CFBundleIconFile</key><string>Vera</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$BUILD_NUM</string>
  <key>LSMinimumSystemVersion</key><string>26.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
  <key>NSMicrophoneUsageDescription</key><string>Vera listens to your voice for hands-free conversation.</string>
</dict>
</plist>
PLIST

# 4. Ad-hoc sign (no Developer ID on personal Macs) + verify
echo "==> Ad-hoc codesign"
codesign --force --deep --sign - "$APP"
codesign --verify --verbose "$APP"

rm -rf "$ICONSET"
echo "==> Done: $APP_ROOT/$APP  (v$VERSION, build $BUILD_NUM)"
