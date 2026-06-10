"""Heartbeat outcome log — what Vera tried/observed each tick, so she can dedup
(not re-propose the same thing) and so there's an audit trail of the try→observe→remember loop."""
import json
import os
import sqlite3
import time

DB_PATH = os.environ.get("HEARTBEAT_DB_PATH", "/data/heartbeat.db")


def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS outcome (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,
                kind TEXT,        -- 'learn' | 'propose' | 'refine' | 'confirmed' | 'dismissed'
                detail TEXT,      -- e.g. 'ha.service:climate.office' or a topic
                extra TEXT        -- json
            )"""
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_oc_ts ON outcome(ts)")


def log(kind, detail, extra=None):
    init()
    with _conn() as c:
        c.execute("INSERT INTO outcome(ts,kind,detail,extra) VALUES(?,?,?,?)",
                  (int(time.time()), kind, detail, json.dumps(extra) if extra is not None else None))


def recent(hours=24):
    init()
    cutoff = int(time.time()) - hours * 3600
    with _conn() as c:
        rows = c.execute("SELECT ts,kind,detail,extra FROM outcome WHERE ts > ? ORDER BY id DESC",
                         (cutoff,)).fetchall()
    return [{"ts": r["ts"], "kind": r["kind"], "detail": r["detail"],
             "extra": json.loads(r["extra"]) if r["extra"] else None} for r in rows]
