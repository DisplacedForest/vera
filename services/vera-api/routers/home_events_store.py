"""Whole-house event store — every HA state change + automation/script fire, append-only.

SQLite at /data/home_events.db. The raw substrate the home-rhythm model learns from; it
outlives HA's ~10-day recorder purge because it's ours. Mirrors the pulse/knowledge store pattern.
"""
import json
import os
import sqlite3
import time

DB_PATH = os.environ.get("HOME_EVENTS_DB_PATH", "/data/home_events.db")
# The home model re-mines this raw window nightly and the derived patterns persist in
# home_model.db, so the raw stream only needs the recent signal window. 30 days = ~4 samples per
# weekday (enough for weekly rhythms); storage is explicitly not the constraint, so we keep the
# upper end of the 14–30 range to maximize signal.
RETAIN_DAYS = int(os.environ.get("HOME_EVENTS_RETAIN_DAYS", "30"))


def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")  # frequent small writes from the live event stream
    return c


def init():
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,            -- unix seconds (HA event time_fired)
                event_type TEXT,       -- state_changed | automation_triggered | script_started
                entity_id TEXT,
                domain TEXT,
                old_state TEXT,        -- prior state string (state_changed)
                new_state TEXT,        -- new state string (or the automation/script name)
                attrs TEXT,            -- json: new_state attributes — the real values
                context TEXT           -- json: {id,parent_id,user_id} — what triggered it
            )
            """
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_ev_ts ON events(ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ev_entity ON events(entity_id, ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ev_type ON events(event_type, ts)")


def insert(ev: dict):
    init()
    with _conn() as c:
        c.execute(
            """INSERT INTO events(ts, event_type, entity_id, domain, old_state, new_state, attrs, context)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                ev.get("ts") or int(time.time()),
                ev.get("event_type"),
                ev.get("entity_id"),
                ev.get("domain"),
                ev.get("old_state"),
                ev.get("new_state"),
                json.dumps(ev.get("attrs")) if ev.get("attrs") is not None else None,
                json.dumps(ev.get("context")) if ev.get("context") is not None else None,
            ),
        )


def _row(r) -> dict:
    return {
        "id": r["id"], "ts": r["ts"], "event_type": r["event_type"], "entity_id": r["entity_id"],
        "domain": r["domain"], "old_state": r["old_state"], "new_state": r["new_state"],
        "attrs": json.loads(r["attrs"]) if r["attrs"] else None,
        "context": json.loads(r["context"]) if r["context"] else None,
    }


def recent(limit: int = 100, entity_id: str | None = None, event_type: str | None = None,
           since: int | None = None) -> list[dict]:
    init()
    where, args = [], []
    if entity_id:
        where.append("entity_id = ?"); args.append(entity_id)
    if event_type:
        where.append("event_type = ?"); args.append(event_type)
    if since:
        where.append("ts >= ?"); args.append(int(since))
    sql = ("SELECT * FROM events" + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY ts DESC, id DESC LIMIT ?")
    args.append(limit)
    with _conn() as c:
        rows = c.execute(sql, args).fetchall()
    return [_row(r) for r in rows]


def stats() -> dict:
    init()
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) n FROM events").fetchone()["n"]
        span = c.execute("SELECT MIN(ts) a, MAX(ts) b FROM events").fetchone()
        by_type = {r["event_type"]: r["n"]
                   for r in c.execute("SELECT event_type, COUNT(*) n FROM events GROUP BY event_type")}
        by_domain = {r["domain"]: r["n"] for r in c.execute(
            "SELECT domain, COUNT(*) n FROM events GROUP BY domain ORDER BY n DESC LIMIT 20")}
    return {"total": total, "first_ts": span["a"], "last_ts": span["b"],
            "by_type": by_type, "top_domains": by_domain}


def purge(retain_days: int | None = None) -> int:
    """Drop events older than the retention window. Returns count deleted."""
    init()
    cutoff = int(time.time()) - (retain_days or RETAIN_DAYS) * 86400
    with _conn() as c:
        cur = c.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        return cur.rowcount
