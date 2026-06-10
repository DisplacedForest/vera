#!/usr/bin/env bash
# Run the vera-voice STT/TTS service. Binds 0.0.0.0 so the Vera app on the voice host AND
# other machines can reach it over the LAN (http://<voice host>:8131).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
VENV="${VERA_VOICE_VENV:-$HOME/vera-voice/.venv}"
HOST="${VERA_VOICE_HOST:-0.0.0.0}"
PORT="${VERA_VOICE_PORT:-8131}"
exec "$VENV/bin/python" -m uvicorn app:app --host "$HOST" --port "$PORT"
