#!/usr/bin/env bash
# Deploy vera-voice on the voice host (Apple Silicon mac) — the whole ritual in one script.
#
# launchd cannot read ~/Desktop (TCC-protected), so the runtime is deployed OUT of the
# repo into ~/vera-voice; the repo stays the source of truth. Two venvs by design:
#   .venv          — the batch HTTP service (:8131): POST /tts, /stt, /health
#   .venv-wyoming  — the streaming Wyoming servers (ASR :10300, TTS :10200)
# They're separate so streaming-stack upgrades can never break the batch path (and vice
# versa). Override locations with VERA_VOICE_HOME / VERA_VOICE_VENV / VERA_VOICE_VENV_WYOMING.
#
#   usage: scripts/deploy-vera-voice.sh            # deploy + venvs + launchd agents
#          scripts/deploy-vera-voice.sh --no-launchd   # deploy + venvs only
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO/services/vera-voice"
DEST="${VERA_VOICE_HOME:-$HOME/vera-voice}"
VENV="${VERA_VOICE_VENV:-$DEST/.venv}"
VENV_WY="${VERA_VOICE_VENV_WYOMING:-$DEST/.venv-wyoming}"
PYTHON="${VERA_VOICE_PYTHON:-python3}"

echo "==> Deploy runtime to $DEST"
mkdir -p "$DEST"
cp "$SRC"/{app.py,asr_server.py,tts_server.py,run.sh,run_asr.sh,run_tts.sh,requirements.txt,requirements-wyoming.txt} "$DEST/"
rm -rf "$DEST/engines"
cp -R "$SRC/engines" "$DEST/engines"
chmod +x "$DEST"/run.sh "$DEST"/run_asr.sh "$DEST"/run_tts.sh

venv_install() {  # <venv> <requirements> — create if absent; tolerate pip-less (uv-built) venvs
  local venv="$1" reqs="$2"
  [ -x "$venv/bin/python" ] || "$PYTHON" -m venv "$venv"
  "$venv/bin/python" -m pip --version >/dev/null 2>&1 || "$venv/bin/python" -m ensurepip --upgrade
  "$venv/bin/python" -m pip install --quiet --upgrade pip
  "$venv/bin/python" -m pip install --quiet -r "$reqs"
}

echo "==> Batch venv ($VENV)"
venv_install "$VENV" "$DEST/requirements.txt"

echo "==> Wyoming venv ($VENV_WY)"
venv_install "$VENV_WY" "$DEST/requirements-wyoming.txt"

if [ "${1:-}" != "--no-launchd" ]; then
  echo "==> Install launchd agents (start at login, kept alive)"
  "$REPO/scripts/install-launchd.sh" \
    "$SRC/vera-voice.plist.template" \
    "$SRC/vera-voice-asr.plist.template" \
    "$SRC/vera-voice-tts.plist.template"
fi

echo "==> Done. Verify:"
echo "    curl -s http://localhost:\${VERA_VOICE_PORT:-8131}/health"
echo "    launchctl list | grep com.vera.voice"
