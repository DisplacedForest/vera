#!/usr/bin/env bash
# Run the vera-voice Wyoming ASR server: streaming STT over the Wyoming
# protocol. Parakeet-MLX (incremental) by default; Whisper fallback via VERA_STT_ENGINE.
# Binds 0.0.0.0:10300 so the Vera app AND Wyoming satellites can reach it over the LAN.
# Uses the dedicated .venv-wyoming so it never disturbs the legacy batch service (:8131).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
VENV="${VERA_VOICE_VENV_WYOMING:-$HOME/vera-voice/.venv-wyoming}"
export VERA_STT_ENGINE="${VERA_STT_ENGINE:-parakeet}"
export VERA_ASR_PORT="${VERA_ASR_PORT:-10300}"
exec "$VENV/bin/python" asr_server.py
