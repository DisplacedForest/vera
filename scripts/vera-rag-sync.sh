#!/bin/bash
set -uo pipefail

ENV_FILE="${VERA_API_ENV_FILE:-/mnt/user/appdata/vera-api/.env}"
if [ -z "${VERA_API_BASE:-}" ] && [ -r "$ENV_FILE" ]; then
  VERA_API_BASE="$(grep -E '^VERA_API_BASE=' "$ENV_FILE" | tail -n 1 | cut -d= -f2-)"
fi
export VERA_API_BASE="${VERA_API_BASE:-}"
export REFERENCE_ROOT="${REFERENCE_ROOT:-/mnt/user/reference}"

exec /usr/bin/python3 "$(dirname "$0")/rag-sync.py" --all
