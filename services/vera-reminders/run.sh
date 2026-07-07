#!/usr/bin/env bash
# Run the vera-reminders EventKit bridge. Binds 0.0.0.0 so vera-api on another
# machine can reach it over the LAN (http://<bridge host>:8132).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
VENV="${VERA_REMINDERS_VENV:-$HOME/vera-reminders/.venv}"
HOST="${VERA_REMINDERS_HOST:-0.0.0.0}"
PORT="${VERA_REMINDERS_PORT:-8132}"
exec "$VENV/bin/python" -m uvicorn app:app --host "$HOST" --port "$PORT"
