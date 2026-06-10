#!/bin/bash
# Cron wrapper for rag-sync.py — EXAMPLE-DEPLOYMENT GLUE (paths/creds for one docker host);
# the portable mechanism lives in rag-sync.py.
# Reconcile the reference corpus into OWUI knowledge collections.
# Standing RAG pipeline: drop/update/remove a doc in $REFERENCE_ROOT/<domain>/ and
# this makes the change queryable. Idempotent — a run with no disk changes does nothing.
# Run daily from cron; the corpus is stable reference, so daily is plenty.
# Auth: OWUI_KEY read from vera-api's .env (bearer) — override the path via VERA_API_ENV_FILE.
export OWUI_BASE="${OWUI_BASE:-http://localhost:6590}"
ENV_FILE="${VERA_API_ENV_FILE:-/mnt/user/appdata/vera-api/.env}"
export OWUI_KEY=$(grep -E '^OWUI_KEY=' "$ENV_FILE" | cut -d= -f2-)
export REFERENCE_ROOT="${REFERENCE_ROOT:-/mnt/user/reference}"
/usr/bin/python3 "$(dirname "$0")/rag-sync.py" --all
