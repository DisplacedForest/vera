#!/usr/bin/env bash
# Run the vera-voice Wyoming TTS server: Kokoro (mlx-audio) over the Wyoming
# protocol. Binds 0.0.0.0:10200 so the Vera app AND Wyoming satellites can reach it.
# Uses the dedicated .venv-wyoming so it never disturbs the legacy batch service (:8131).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
VENV="${VERA_VOICE_VENV_WYOMING:-$HOME/vera-voice/.venv-wyoming}"
export VERA_TTS_PORT="${VERA_TTS_PORT:-10200}"
export VERA_TTS_VOICE="${VERA_TTS_VOICE:-af_heart}"
exec "$VENV/bin/python" tts_server.py
