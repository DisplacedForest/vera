#!/bin/sh
# Copy gate: user-facing app strings never contain em dashes.
# Scans Swift string literals under apps/vera-mac/Sources and fails listing
# any line where an em dash appears between double quotes. Code comments are
# not literals and pass. vera-api is reviewed by convention instead (prompts
# and log lines there legitimately use the character).
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/apps/vera-mac/Sources"

offenders=$(grep -rn '—' "$SRC" --include='*.swift' | grep -E '"[^"]*—[^"]*"' || true)

if [ -n "$offenders" ]; then
    echo "Em dash found in app string literals (use periods, commas, or parentheses; N/A for placeholders):" >&2
    echo "$offenders" >&2
    exit 1
fi

echo "check-copy: OK (no em dashes in app string literals)"
