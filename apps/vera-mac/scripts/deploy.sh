#!/usr/bin/env bash
# Package Vera.app, install it to /Applications, and (if reachable) push it to a second Mac.
# Remote target: set VERA_STUDIO_HOST (an ssh alias or user@host). Skipped if unset/unreachable.
set -euo pipefail

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_ROOT"

"$APP_ROOT/scripts/package.sh"

APP="$APP_ROOT/build/Vera.app"

echo "==> Install to /Applications"
rm -rf "/Applications/Vera.app"
cp -R "$APP" "/Applications/Vera.app"
# This macOS `xattr` has no -r flag; clear xattrs per-file via find instead.
find "/Applications/Vera.app" -exec xattr -c {} + 2>/dev/null || true
touch "/Applications/Vera.app"   # bump mtime so LaunchServices/Dock re-read the .icns
echo "    installed /Applications/Vera.app"

HOST="${VERA_STUDIO_HOST:-}"
if [ -z "$HOST" ]; then
  echo "==> Remote: VERA_STUDIO_HOST not set — skipping remote deploy."
  exit 0
fi
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$HOST" true 2>/dev/null; then
  echo "==> Remote ($HOST) unreachable — skipping remote deploy."
  exit 0
fi

echo "==> Deploy to remote ($HOST)"
ssh "$HOST" 'rm -rf /tmp/Vera.app'
scp -rq "$APP" "$HOST:/tmp/Vera.app"
ssh "$HOST" 'rm -rf /Applications/Vera.app && cp -R /tmp/Vera.app /Applications/Vera.app && rm -rf /tmp/Vera.app && find /Applications/Vera.app -exec xattr -c {} + 2>/dev/null; touch /Applications/Vera.app'
echo "    deployed to $HOST:/Applications/Vera.app"
