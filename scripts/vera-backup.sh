#!/bin/bash
# vera-backup — scheduled backup of Vera's irreplaceable state.
# Unraid-flavored REFERENCE IMPLEMENTATION: written for an Unraid host (run as root, deployed
# to /boot/config/scripts/, scheduled nightly via /etc/cron.d/ persisted by /boot/config/go).
# Adapt paths and scheduling for other hosts; set VERA_APPDATA_ROOT if container appdata lives
# elsewhere. Irreplaceable-first; never aborts the whole run if one component fails — the OWUI
# DB (Vera's learned self) is priority.
set -uo pipefail

DEST_ROOT="${VERA_BACKUP_DEST:-/mnt/user/backups/vera}"
KEEP=14                      # nightly snapshots to retain
STAMP="$(date +%F_%H%M)"
DEST="$DEST_ROOT/$STAMP"
APP="${VERA_APPDATA_ROOT:-/mnt/user/appdata}"
mkdir -p "$DEST"

log() { echo "[$(date +%T)] $*"; }
fail=0

# 1. OWUI sqlite DB — the crown jewel (memories, chats, functions, sk- keys). Consistent snapshot.
if sqlite3 "$APP/open-webui/webui.db" ".backup '$DEST/webui.db'" 2>/dev/null; then
  sync
  if [ "$(sqlite3 "$DEST/webui.db" 'PRAGMA integrity_check' 2>/dev/null)" = "ok" ]; then
    log "OK   webui.db ($(du -h "$DEST/webui.db" | cut -f1), integrity ok)"
  else
    log "WARN webui.db integrity check not ok"; fail=1
  fi
else
  log "FAIL webui.db backup"; fail=1
fi

# 2. OWUI rest of data dir (uploads, vector store) — best-effort, large.
tar czf "$DEST/owui-data.tgz" -C "$APP/open-webui" \
  --exclude='webui.db' --exclude='webui.db-wal' --exclude='webui.db-shm' . 2>/dev/null \
  && log "OK   owui-data.tgz ($(du -h "$DEST/owui-data.tgz" | cut -f1))" || { log "FAIL owui-data.tgz"; fail=1; }

# 3. Plane Postgres (optional). Discover the DB container; creds via its own env — never logged.
#    Absent Plane is a SKIP (not a failure); an empty dump from a *present* Plane is a real FAIL.
PLANE_DB="$(docker ps --format '{{.Names}}' | grep -Ei 'plane.*(db|postgres)' | head -1)"
if [ -n "$PLANE_DB" ]; then
  docker exec "$PLANE_DB" sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' 2>/dev/null | gzip > "$DEST/plane-db.sql.gz"
  if [ "$(stat -c%s "$DEST/plane-db.sql.gz" 2>/dev/null || echo 0)" -gt 100 ]; then
    log "OK   plane-db.sql.gz ($(du -h "$DEST/plane-db.sql.gz" | cut -f1))"
  else
    log "FAIL plane-db (empty dump from $PLANE_DB)"; fail=1
  fi
else
  rm -f "$DEST/plane-db.sql.gz"
  log "SKIP plane-db (no Plane DB container present)"
fi

# 4. vera-api config (.env, routers), llama-swap config, n8n.
tar czf "$DEST/vera-api.tgz"  -C "$APP" vera-api  2>/dev/null && log "OK   vera-api.tgz"  || { log "FAIL vera-api.tgz"; fail=1; }
cp "$APP/llama-swap/config.yaml" "$DEST/llama-swap-config.yaml" 2>/dev/null && log "OK   llama-swap config" || log "WARN llama-swap config"
tar czf "$DEST/n8n.tgz" -C "$APP" n8n 2>/dev/null && log "OK   n8n.tgz ($(du -h "$DEST/n8n.tgz" | cut -f1))" || { log "FAIL n8n.tgz"; fail=1; }

# Manifest + checksums (a backup you can't verify isn't a backup).
( cd "$DEST" && sha256sum -- * > SHA256SUMS 2>/dev/null )
echo "vera-backup $STAMP  host=$(hostname)  status=$([ $fail -eq 0 ] && echo OK || echo PARTIAL)" > "$DEST/MANIFEST"
log "manifest + checksums written"

# Retention: keep the newest $KEEP, prune older.
ls -1dt "$DEST_ROOT"/*/ 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -rf
log "retention: kept newest $KEEP"

log "done -> $DEST  (status=$([ $fail -eq 0 ] && echo OK || echo PARTIAL))"
exit $fail
