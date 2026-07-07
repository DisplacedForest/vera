#!/usr/bin/env bash
# Deploy vera-reminders on the bridge host (a Mac signed into iCloud).
#
# launchd cannot read ~/Desktop (TCC-protected), so the runtime is deployed OUT of the
# repo into ~/vera-reminders; the repo stays the source of truth. Override locations
# with VERA_REMINDERS_HOME / VERA_REMINDERS_VENV.
#
# First start triggers the macOS Reminders permission prompt, which must be approved
# in a GUI session on this Mac (Screen Sharing works); after that one approval the
# bridge runs headless. Verify with: curl -s localhost:8132/health
#
#   usage: scripts/deploy-vera-reminders.sh              # deploy + venv + launchd
#          scripts/deploy-vera-reminders.sh --no-launchd # deploy + venv only
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO/services/vera-reminders"
DEST="${VERA_REMINDERS_HOME:-$HOME/vera-reminders}"
VENV="${VERA_REMINDERS_VENV:-$DEST/.venv}"
PYTHON="${VERA_REMINDERS_PYTHON:-python3}"

"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "vera-reminders needs Python 3.10+; '$PYTHON' is $("$PYTHON" -V 2>&1)." >&2
  echo "Point VERA_REMINDERS_PYTHON at a newer interpreter." >&2
  exit 1
}

echo "==> Deploy runtime to $DEST"
mkdir -p "$DEST"
cp "$SRC"/{app.py,eventkit_store.py,bundle_main.py,setup.py,run.sh,requirements.txt} "$DEST/"
chmod +x "$DEST/run.sh"

echo "==> Venv ($VENV)"
[ -x "$VENV/bin/python" ] || "$PYTHON" -m venv "$VENV"
"$VENV/bin/python" -m pip --version >/dev/null 2>&1 || "$VENV/bin/python" -m ensurepip --upgrade
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r "$DEST/requirements.txt" py2app

echo "==> Build VeraReminders.app (alias mode; carries the Reminders usage description)"
( cd "$DEST" && rm -rf build dist && "$VENV/bin/python" setup.py py2app -A >/dev/null )

if [ "${1:-}" != "--no-launchd" ]; then
  echo "==> Install launchd agent (start at login, kept alive)"
  "$REPO/scripts/install-launchd.sh" "$SRC/vera-reminders.plist.template"
fi

echo "==> Done. Verify:"
echo "    curl -s http://localhost:\${VERA_REMINDERS_PORT:-8132}/health"
echo "    launchctl list | grep com.vera.reminders"
