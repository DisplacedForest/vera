#!/bin/sh
# OSS leak gate. Fails when household-specific values or secret-shaped strings appear in the
# git-tracked tree, which is what publishes. Static pattern checks always run. Identity-value
# checks run only when the matching variable is provided (CI secrets, an exported shell var, or
# a key set in a local .env), so a fork without secrets still gets the static checks.
#
# Exempt a known-good hit by adding a POSIX-extended regex line to scripts/leak-allow.txt.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ALLOW="scripts/leak-allow.txt"
fail=0

# Read a value from the environment, falling back to a bare KEY=value line in .env.
resolve() {
    _v=$(eval "printf '%s' \"\${$1:-}\"")
    if [ -z "$_v" ] && [ -f .env ]; then
        _v=$(grep -E "^[[:space:]]*$1=" .env 2>/dev/null | head -1 \
            | sed -E "s/^[[:space:]]*$1=//; s/^[\"']//; s/[\"'][[:space:]]*\$//")
    fi
    printf '%s' "$_v"
}

# Drop allowlisted lines from stdin.
allow_filter() {
    if [ -f "$ALLOW" ]; then
        _re=$(grep -vE '^[[:space:]]*(#|$)' "$ALLOW" 2>/dev/null | paste -sd'|' - || true)
        if [ -n "$_re" ]; then grep -vE "$_re" || true; else cat; fi
    else
        cat
    fi
}

# scan <label> <extended-regex>
scan() {
    _hits=$(git grep -nIE -e "$2" -- . ':!scripts/leak-gate.sh' ':!scripts/leak-allow.txt' 2>/dev/null \
        | allow_filter || true)
    if [ -n "$_hits" ]; then
        printf 'LEAK [%s]:\n%s\n\n' "$1" "$_hits" >&2
        fail=1
    fi
}

# git grep uses POSIX ERE; \b is unsupported, so boundaries are spelled out with character classes.
scan "lan-ip"          '(^|[^0-9.])(192\.168|172\.(1[6-9]|2[0-9]|3[01]))\.[0-9]{1,3}\.[0-9]{1,3}'
scan "lan-ip-10"       '(^|[^0-9.])10(\.[0-9]{1,3}){3}([^0-9]|$)'
scan "home-abs-path"   '/Users/[A-Za-z0-9]'
scan "ser-ref-comment" '(#|//|/\*|--).*SER-[0-9]'
scan "owui-key"        'sk-[A-Za-z0-9]{20,}'
scan "jwt"             'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'
scan "private-key"     'BEGIN [A-Z ]*PRIVATE KEY'

for _var in VERA_OWNER_NAME HOME_LOCATION_NAME WEATHER_LAT WEATHER_LON; do
    _val=$(resolve "$_var")
    if [ -n "$_val" ]; then
        _esc=$(printf '%s' "$_val" | sed -E 's/[][(){}.^$*+?|\\/]/\\&/g')
        scan "identity:$_var" "$_esc"
    fi
done

if [ "$fail" -ne 0 ]; then
    echo "leak-gate: FAILED. Remove the value, or add a justified regex to $ALLOW." >&2
    exit 1
fi
echo "leak-gate: OK (tracked tree clean of leak patterns)"
