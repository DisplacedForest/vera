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
        c.execute(
            """CREATE TABLE IF NOT EXISTS skill (
                id TEXT PRIMARY KEY,
                name TEXT,
                description TEXT,
                content TEXT,
                updated INTEGER
            )"""
        )


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


def skill_upsert(sid, name, description, content):
    init()
    with _conn() as c:
        c.execute(
            "INSERT INTO skill(id, name, description, content, updated) VALUES(?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, description=excluded.description, "
            "content=excluded.content, updated=excluded.updated",
            (sid, name or sid, description or "", content, int(time.time())),
        )
    return sid


def skill_get(sid):
    init()
    with _conn() as c:
        r = c.execute("SELECT * FROM skill WHERE id=?", (sid,)).fetchone()
        if r:
            return dict(r)
        rev = c.execute(
            "SELECT content FROM revision WHERE target=? ORDER BY id DESC LIMIT 1",
            (f"skill:{sid}",),
        ).fetchone()
        if not rev:
            return None
        c.execute(
            "INSERT OR IGNORE INTO skill(id, name, description, content, updated) VALUES(?,?,?,?,?)",
            (sid, sid, "", rev["content"], int(time.time())),
        )
        r = c.execute("SELECT * FROM skill WHERE id=?", (sid,)).fetchone()
    return dict(r) if r else None


def skill_list():
    init()
    with _conn() as c:
        rows = c.execute(
            "SELECT id, name, description, length(content) AS size, updated "
            "FROM skill ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]
