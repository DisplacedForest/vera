"""Reconciler state store — SQLite for the self-reconciling Home map.

Three tables in /data/home_reconcile.db:

* `seen_entities` — the entity baseline. Every tracked-domain HA entity ever observed, with a
  `missing_runs` counter so a disappearance is only treated as "removed" after it's been absent for
  several consecutive passes (a flapping `unavailable` entity must NOT read as gone). The first pass
  seeds this silently — otherwise every entity in the house would card as "new".
* `drift` — the open-drift ledger, keyed by a STABLE key (`kind:ref`). Dedups so a standing drift
  isn't re-carded every night, and lets a drift be auto-resolved when the condition clears.
* `runs` — one row per reconcile pass, for the `GET /home/reconcile` audit view.

The pure diff core (`_diff`) takes plain dicts and is unit-tested without a database.
"""

import json
import os
import sqlite3
import time

DB_PATH = os.environ.get("HOME_RECONCILE_DB_PATH", "/data/home_reconcile.db")

# An entity must be absent this many consecutive passes before it counts as removed (flap guard).
REMOVAL_THRESHOLD = int(os.environ.get("RECONCILE_REMOVAL_RUNS", "3"))


def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen_entities (
                entity_id TEXT PRIMARY KEY, domain TEXT, integration TEXT, friendly_name TEXT,
                first_seen INTEGER, last_seen INTEGER, missing_runs INTEGER, status TEXT
            );
            CREATE TABLE IF NOT EXISTS drift (
                key TEXT PRIMARY KEY, kind TEXT, ref TEXT, detail TEXT, status TEXT,
                card_id TEXT, created_at INTEGER, updated_at INTEGER, resolved_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, dry_run INTEGER,
                states_seen INTEGER, new_n INTEGER, removed_n INTEGER, stale_n INTEGER,
                index_total INTEGER, index_ok INTEGER, index_failed INTEGER, index_idle INTEGER,
                auto_resolved INTEGER, carded INTEGER, summary TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_drift_status ON drift(status);
            """
        )


# ---- pure diff core (unit-tested, no DB) -----------------------------------

def _diff(seen: dict, current: set, threshold: int):
    """Diff a baseline against the currently-present entity set.

    `seen`: {entity_id: missing_runs} from the baseline (status='active' rows only).
    `current`: set of entity_ids present right now.
    Returns (new_ids, removed_ids, next_missing) where:
      * new_ids      — present now, never in the baseline.
      * removed_ids  — in the baseline, absent now, and over the consecutive-miss threshold.
      * next_missing — {entity_id: missing_runs} for entities still tracked (present reset to 0;
                       absent incremented; the just-removed ones drop out).
    """
    new_ids = sorted(current - set(seen))
    removed_ids, next_missing = [], {}
    for eid, miss in seen.items():
        if eid in current:
            next_missing[eid] = 0
        else:
            m = miss + 1
            if m >= threshold:
                removed_ids.append(eid)  # crossed the threshold -> removed (and drops from baseline)
            else:
                next_missing[eid] = m
    for eid in current:  # newly-seen entities enter the baseline at 0 misses
        next_missing.setdefault(eid, 0)
    return new_ids, sorted(removed_ids), next_missing


# ---- baseline (seen_entities) ----------------------------------------------

def apply_diff(current_meta: dict, threshold: int = REMOVAL_THRESHOLD):
    """Reconcile the persisted baseline against the live entity set.

    `current_meta`: {entity_id: {"domain","integration","friendly_name"}} for tracked entities.
    On the very first pass (empty baseline) every entity is seeded silently and nothing is reported.
    Returns {"first_run": bool, "new": [meta...], "removed": [meta...]}.
    """
    init()
    now = int(time.time())
    with _conn() as c:
        rows = c.execute("SELECT entity_id, missing_runs FROM seen_entities WHERE status='active'").fetchall()
        first_run = len(rows) == 0 and c.execute("SELECT COUNT(*) n FROM seen_entities").fetchone()["n"] == 0
        seen = {r["entity_id"]: r["missing_runs"] for r in rows}
        prior_meta = {r["entity_id"]: r for r in c.execute("SELECT * FROM seen_entities").fetchall()}

        if first_run:
            for eid, m in current_meta.items():
                c.execute(
                    "INSERT INTO seen_entities VALUES(?,?,?,?,?,?,?,?)",
                    (eid, m["domain"], m["integration"], m["friendly_name"], now, now, 0, "active"),
                )
            return {"first_run": True, "new": [], "removed": []}

        new_ids, removed_ids, next_missing = _diff(seen, set(current_meta), threshold)

        for eid, m in next_missing.items():
            if eid in current_meta:
                meta = current_meta[eid]
                c.execute(
                    """INSERT INTO seen_entities VALUES(?,?,?,?,?,?,?,'active')
                       ON CONFLICT(entity_id) DO UPDATE SET last_seen=?, missing_runs=0,
                         status='active', friendly_name=excluded.friendly_name,
                         integration=excluded.integration, domain=excluded.domain""",
                    (eid, meta["domain"], meta["integration"], meta["friendly_name"],
                     now, now, 0, now),
                )
            else:  # still tracked but absent — bump the miss counter
                c.execute("UPDATE seen_entities SET missing_runs=?, last_seen=last_seen WHERE entity_id=?",
                          (m, eid))
        for eid in removed_ids:
            c.execute("UPDATE seen_entities SET status='gone', missing_runs=? WHERE entity_id=?",
                      (threshold, eid))

        def _meta(eid):
            r = current_meta.get(eid)
            if r:
                return {"entity_id": eid, **r}
            p = prior_meta.get(eid)
            return {"entity_id": eid, "domain": p["domain"] if p else eid.split(".")[0],
                    "integration": p["integration"] if p else None,
                    "friendly_name": p["friendly_name"] if p else eid}

        return {"first_run": False,
                "new": [_meta(e) for e in new_ids],
                "removed": [_meta(e) for e in removed_ids]}


def preview_diff(current_meta: dict, threshold: int = REMOVAL_THRESHOLD):
    """Read-only counterpart to apply_diff — computes new/removed without touching the baseline.
    Used by dry-run reconciles so a preview never advances the miss counters."""
    init()
    with _conn() as c:
        rows = c.execute("SELECT * FROM seen_entities WHERE status='active'").fetchall()
        total = c.execute("SELECT COUNT(*) n FROM seen_entities").fetchone()["n"]
    if total == 0:
        return {"first_run": True, "new": [], "removed": []}
    seen = {r["entity_id"]: r["missing_runs"] for r in rows}
    prior_meta = {r["entity_id"]: r for r in rows}
    new_ids, removed_ids, _ = _diff(seen, set(current_meta), threshold)

    def _meta(eid):
        r = current_meta.get(eid)
        if r:
            return {"entity_id": eid, **r}
        p = prior_meta.get(eid)
        return {"entity_id": eid, "domain": p["domain"] if p else eid.split(".")[0],
                "integration": p["integration"] if p else None,
                "friendly_name": p["friendly_name"] if p else eid}

    return {"first_run": False,
            "new": [_meta(e) for e in new_ids],
            "removed": [_meta(e) for e in removed_ids]}


# ---- drift ledger ----------------------------------------------------------

def open_drift(key: str):
    init()
    with _conn() as c:
        r = c.execute("SELECT * FROM drift WHERE key=? AND status IN ('open','carded')", (key,)).fetchone()
    return dict(r) if r else None


def record_drift(key, kind, ref, detail, status="open", card_id=None):
    """Upsert a drift row. Returns True if this is a NEW open drift (caller should card it)."""
    init()
    now = int(time.time())
    existing = open_drift(key)
    with _conn() as c:
        if existing:
            c.execute("UPDATE drift SET detail=?, updated_at=?, status=?, card_id=COALESCE(?,card_id) WHERE key=?",
                      (detail, now, status, card_id, key))
            return False
        c.execute("INSERT OR REPLACE INTO drift VALUES(?,?,?,?,?,?,?,?,?)",
                  (key, kind, ref, detail, status, card_id, now, now, None))
        return True


def set_card(key, card_id):
    init()
    with _conn() as c:
        c.execute("UPDATE drift SET card_id=?, status='carded', updated_at=? WHERE key=?",
                  (card_id, int(time.time()), key))


def resolve_absent(active_keys: set):
    """Mark every open/carded drift whose key is NOT in active_keys as resolved (condition cleared)."""
    init()
    now = int(time.time())
    with _conn() as c:
        rows = c.execute("SELECT key FROM drift WHERE status IN ('open','carded')").fetchall()
        stale = [r["key"] for r in rows if r["key"] not in active_keys]
        for k in stale:
            c.execute("UPDATE drift SET status='resolved', resolved_at=?, updated_at=? WHERE key=?",
                      (now, now, k))
    return stale


def list_open(limit: int = 100):
    init()
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM drift WHERE status IN ('open','carded') ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---- runs ------------------------------------------------------------------

def record_run(dry_run, stats: dict):
    init()
    with _conn() as c:
        c.execute(
            """INSERT INTO runs(ts,dry_run,states_seen,new_n,removed_n,stale_n,index_total,
               index_ok,index_failed,index_idle,auto_resolved,carded,summary)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (int(time.time()), 1 if dry_run else 0, stats.get("states_seen", 0),
             stats.get("new_n", 0), stats.get("removed_n", 0), stats.get("stale_n", 0),
             stats.get("index_total", 0), stats.get("index_ok", 0), stats.get("index_failed", 0),
             stats.get("index_idle", 0), stats.get("auto_resolved", 0), stats.get("carded", 0),
             json.dumps(stats)),
        )


def last_run():
    init()
    with _conn() as c:
        r = c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    if not r:
        return None
    d = dict(r)
    d["summary"] = json.loads(d.get("summary") or "{}")
    return d
