#!/bin/bash
# vera-backup — scheduled backup of Vera's irreplaceable state.
# Unraid-flavored REFERENCE IMPLEMENTATION: written for an Unraid host (run as root, deployed
# to /boot/config/scripts/, scheduled nightly via /etc/cron.d/ persisted by /boot/config/go).
# Adapt paths and scheduling for other hosts; set VERA_APPDATA_ROOT if container appdata lives
# elsewhere. Irreplaceable-first; never aborts the whole run if one component fails.
set -uo pipefail

DEST_ROOT="${VERA_BACKUP_DEST:-/mnt/user/backups/vera}"
KEEP=14                      # nightly snapshots to retain
STAMP="$(date +%F_%H%M)"
DEST="$DEST_ROOT/$STAMP"
APP="${VERA_APPDATA_ROOT:-/mnt/user/appdata}"
CONTAINER="${VERA_CONTAINER:-vera-api}"
mkdir -p "$DEST"

log() { echo "[$(date +%T)] $*"; }
fail=0

if [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" = "true" ]; then
  if docker exec "$CONTAINER" python3 -c '
import os, sqlite3, shutil, sys

def die(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)

src, dst = "/data", "/tmp/vera-db-snap"
shutil.rmtree(dst, ignore_errors=True)
count = 0
for root, dirs, files in os.walk(src, onerror=lambda e: die(str(e))):
    for f in files:
        if not f.endswith(".db"):
            continue
        p = os.path.join(root, f)
        out = os.path.join(dst, os.path.relpath(p, src))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        s = sqlite3.connect(p)
        d = sqlite3.connect(out)
        try:
            with d:
                s.backup(d)
            if d.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                die(f"{p}: snapshot failed quick_check")
        except sqlite3.Error as e:
            die(f"{p}: {e}")
        finally:
            d.close()
            s.close()
        count += 1
if count == 0:
    die("no databases found under /data")
'; then
    docker exec "$CONTAINER" tar czf - --exclude="*.db" --exclude="*.db-wal" --exclude="*.db-shm" --exclude="*.db-journal" -C /data . > "$DEST/vera-data.tgz"
    rc=$?
    if [ "$rc" -le 1 ] && [ -s "$DEST/vera-data.tgz" ]; then
      log "OK   vera-data.tgz ($(du -h "$DEST/vera-data.tgz" | cut -f1))"
    else
      log "FAIL vera-data.tgz stream from $CONTAINER (tar rc=$rc)"; fail=1
    fi
    docker exec "$CONTAINER" tar czf - -C /tmp/vera-db-snap . > "$DEST/vera-data-dbs.tgz"
    rc=$?
    if [ "$rc" -eq 0 ] && [ "$(tar tzf "$DEST/vera-data-dbs.tgz" 2>/dev/null | grep -c '\.db$')" -gt 0 ]; then
      log "OK   vera-data-dbs.tgz ($(du -h "$DEST/vera-data-dbs.tgz" | cut -f1))"
    else
      log "FAIL vera-data-dbs.tgz from $CONTAINER (tar rc=$rc)"; fail=1
    fi
  else
    log "FAIL vera-data db snapshot in $CONTAINER"; fail=1
  fi
else
  log "FAIL $CONTAINER is not running; its native stores were not backed up"; fail=1
fi

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

# 3. vera-api config (.env, routers), llama-swap config, n8n.
tar czf "$DEST/vera-api.tgz"  -C "$APP" vera-api  2>/dev/null && log "OK   vera-api.tgz"  || { log "FAIL vera-api.tgz"; fail=1; }
cp "$APP/llama-swap/config.yaml" "$DEST/llama-swap-config.yaml" 2>/dev/null && log "OK   llama-swap config" || log "WARN llama-swap config"
tar czf "$DEST/n8n.tgz" -C "$APP" n8n 2>/dev/null && log "OK   n8n.tgz ($(du -h "$DEST/n8n.tgz" | cut -f1))" || { log "FAIL n8n.tgz"; fail=1; }

# Manifest + checksums (a backup you can't verify isn't a backup).
( cd "$DEST" && sha256sum -- * > SHA256SUMS 2>/dev/null )
echo "vera-backup $STAMP  host=$(hostname)  status=$([ $fail -eq 0 ] && echo OK || echo PARTIAL)" > "$DEST/MANIFEST"
echo "vera-data.tgz  source=$CONTAINER:/data non-database tree, extract with tar -C /data  $([ -s "$DEST/vera-data.tgz" ] && echo present || echo missing)" >> "$DEST/MANIFEST"
echo "vera-data-dbs.tgz  source=$CONTAINER:/data sqlite snapshots, extract with tar -C /data after vera-data.tgz  $([ -s "$DEST/vera-data-dbs.tgz" ] && echo present || echo missing)" >> "$DEST/MANIFEST"
log "manifest + checksums written"

# Retention: keep the newest $KEEP, prune older.
ls -1dt "$DEST_ROOT"/*/ 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -rf
log "retention: kept newest $KEEP"

log "done -> $DEST  (status=$([ $fail -eq 0 ] && echo OK || echo PARTIAL))"
exit $fail
