#!/bin/sh
# Copy gate: user-facing app strings never contain em dashes. Scans Swift sources under
# apps/vera-mac/Sources and fails on any em dash that is not preceded by a comment marker
# on its line, which covers single-line AND triple-quoted multiline string literals.
# Comments (// , /// , /*) legitimately use the character and are exempt. vera-api is
# reviewed by convention (its prompts and log lines legitimately use the character).
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/apps/vera-mac/Sources"

offenders=$(
    find "$SRC" -name '*.swift' -print0 | xargs -0 awk '
    {
        di = index($0, "—")
        if (di == 0) next
        ci = index($0, "//")
        bi = index($0, "/*")
        cstart = 0
        if (ci > 0) cstart = ci
        if (bi > 0 && (cstart == 0 || bi < cstart)) cstart = bi
        if (cstart == 0 || di < cstart) printf "%s:%d: %s\n", FILENAME, FNR, $0
    }'
)

if [ -n "$offenders" ]; then
    echo "Em dash found in app copy (use periods, commas, or parentheses; comments are exempt):" >&2
    echo "$offenders" >&2
    exit 1
fi

echo "check-copy: OK (no em dashes in app copy outside comments)"
