"""Self-authoring revision log — version history for Vera's self-edited docs.

OWUI Skills holds the *live* copy of each skill/heartbeat; this holds the **history** so every
self-authored change is auditable + revertible. One row per version written. Mirrors the simple
append-log pattern used elsewhere.
"""
import os
import sqlite3
import time

DB_PATH = os.environ.get("AUTHORING_DB_PATH", "/data/authoring.db")


def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS revision (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target TEXT,        -- e.g. 'skill:heartbeat'
                content TEXT,
                note TEXT,
                ts INTEGER
            )"""
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_rev_target ON revision(target)")


def snapshot(target, content, note=None):
    init()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO revision(target, content, note, ts) VALUES(?,?,?,?)",
            (target, content, note, int(time.time())),
        )
        return cur.lastrowid


def revisions(target, limit=20):
    init()
    with _conn() as c:
        rows = c.execute(
            "SELECT id, target, note, ts, length(content) AS size FROM revision "
            "WHERE target=? ORDER BY id DESC LIMIT ?",
            (target, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get(rev_id):
    init()
    with _conn() as c:
        r = c.execute("SELECT * FROM revision WHERE id=?", (rev_id,)).fetchone()
    return dict(r) if r else None
