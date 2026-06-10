#!/bin/sh
# Nightly Dreaming — runs ON THE MACHINE THAT SERVES THE DREAM/CODER LLM.
#
# That machine owns the on-demand coder model server, so the orchestration lives here:
# bring the coder up, trigger vera-api's light/deep/REM consolidation, then release the
# model. The main assistant LLM is never touched. This is one of the two jobs that stays
# OUTSIDE vera-api's built-in scheduler (it must start a local model server).
#
# Deploy: copy to ~/.vera/vera-dream.sh (chmod +x) and install the launchd timer:
#   scripts/install-launchd.sh scripts/vera-dream.plist.template
# Config: put `KNOWLEDGE_AGENT_TOKEN=...` and `VERA_API=http://<vera-api host>:8089`
#   (matching vera-api's .env) in ~/.vera/dream.env.
set -eu

[ -f "$HOME/.vera/dream.env" ] && . "$HOME/.vera/dream.env"
VERA_API="${VERA_API:-http://localhost:8089}"
CODER="$HOME/.vera/vera-coder.sh"
LOG=/tmp/vera-dream.log
STAMP="$(date '+%F %T')"

# Always release the coder afterwards, even if the dream call fails.
trap 'sh "$CODER" stop >/dev/null 2>&1 || true' EXIT

sh "$CODER" ensure

# 1) Consolidation + dream journal (light/deep/REM).
RESP=$(curl -sf -m 1800 -X POST "$VERA_API/memory/self/dream" \
  -H "Content-Type: application/json" \
  -H "X-Agent-Token: ${KNOWLEDGE_AGENT_TOKEN:-}" \
  -d '{}' 2>&1) || RESP="ERROR: dream request failed"
echo "$STAMP dream $RESP" >> "$LOG"

# 2) Unified grooming session — tends the world-model + knowledge store in one coordinated
#    run and emits a SINGLE reversible digest card.
GROOM=$(curl -sf -m 1800 -X POST "$VERA_API/memory/self/groom_session" \
  -H "Content-Type: application/json" \
  -H "X-Agent-Token: ${KNOWLEDGE_AGENT_TOKEN:-}" \
  -d '{}' 2>&1) || GROOM="ERROR: groom_session request failed"
echo "$STAMP groom $GROOM" >> "$LOG"
