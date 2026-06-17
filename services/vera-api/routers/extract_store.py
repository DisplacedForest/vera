"""Conversation-extraction cursors — one high-water mark per source so the extraction job
ingests only what is new each run and re-running with nothing new is a no-op.

A cursor is `(last_ts, last_id)`: the timestamp of the newest conversation already ingested
from that source, plus its id for tie-breaking equal timestamps. Mirrors the other
`*_store.py` modules (env-bound SQLite path, `_conn()`, idempotent `init()`)."""
import os
import sqlite3
import time

DB_PATH = os.environ.get("EXTRACT_DB_PATH", "/data/extract/cursors.db")


def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS cursor (
                   source TEXT PRIMARY KEY,
                   last_ts INTEGER,
                   last_id TEXT,
                   updated_at INTEGER
               )"""
        )


def get_cursor(source):
    """The source's high-water mark; a never-seen source reads as the epoch (ingest all)."""
    init()
    with _conn() as c:
        r = c.execute("SELECT last_ts, last_id FROM cursor WHERE source=?", (source,)).fetchone()
    return {"last_ts": r["last_ts"] if r else 0, "last_id": r["last_id"] if r else None}


def set_cursor(source, last_ts, last_id=None):
    init()
    with _conn() as c:
        c.execute(
            """INSERT INTO cursor(source,last_ts,last_id,updated_at) VALUES(?,?,?,?)
               ON CONFLICT(source) DO UPDATE SET
                 last_ts=excluded.last_ts, last_id=excluded.last_id, updated_at=excluded.updated_at""",
            (source, int(last_ts), last_id, int(time.time())),
        )
