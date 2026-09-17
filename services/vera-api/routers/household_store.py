import os
import sqlite3
import time
import uuid

DB_PATH = os.environ.get("HOUSEHOLD_DB_PATH", "/data/household.db")


def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS member (
                id TEXT PRIMARY KEY,
                name TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER
            )
            """
        )
        _ensure_columns(c)
        c.execute("CREATE INDEX IF NOT EXISTS idx_member_enabled ON member(enabled)")
        if c.execute("SELECT COUNT(*) FROM member").fetchone()[0] == 0:
            _seed_owner(c)


COLUMNS = {"name": "TEXT", "enabled": "INTEGER NOT NULL DEFAULT 1", "created_at": "INTEGER"}


def _ensure_columns(c):
    have = {r["name"] for r in c.execute("PRAGMA table_info(member)").fetchall()}
    for name, decl in COLUMNS.items():
        if name not in have:
            c.execute(f"ALTER TABLE member ADD COLUMN {name} {decl}")


def _seed_owner(c):
    from .identity import owner_id, owner_name
    c.execute("INSERT OR IGNORE INTO member(id, name, enabled, created_at) VALUES(?,?,1,?)",
              (owner_id(), owner_name(), int(time.time())))


def _row(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "name": r["name"], "enabled": bool(r["enabled"]),
            "created_at": r["created_at"]}


def members(include_disabled: bool = False) -> list[dict]:
    init()
    sql = "SELECT * FROM member"
    if not include_disabled:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY rowid"
    with _conn() as c:
        return [_row(r) for r in c.execute(sql).fetchall()]


def get(member_id: str) -> dict | None:
    init()
    with _conn() as c:
        r = c.execute("SELECT * FROM member WHERE id = ?", (member_id,)).fetchone()
    return _row(r) if r else None


def add(name: str) -> dict:
    label = (name or "").strip()
    if not label:
        raise ValueError("a household member needs a display name")
    init()
    mid = uuid.uuid4().hex
    with _conn() as c:
        c.execute("INSERT INTO member(id, name, enabled, created_at) VALUES(?,?,1,?)",
                  (mid, label, int(time.time())))
    return get(mid)


def rename(member_id: str, name: str) -> dict:
    label = (name or "").strip()
    if not label:
        raise ValueError("a household member needs a display name")
    _require(member_id)
    with _conn() as c:
        c.execute("UPDATE member SET name = ? WHERE id = ?", (label, member_id))
    return get(member_id)


def disable(member_id: str) -> dict:
    return _set_enabled(member_id, False)


def enable(member_id: str) -> dict:
    return _set_enabled(member_id, True)


def _set_enabled(member_id: str, on: bool) -> dict:
    _require(member_id)
    with _conn() as c:
        c.execute("UPDATE member SET enabled = ? WHERE id = ?", (1 if on else 0, member_id))
    return get(member_id)


def _require(member_id: str):
    if get(member_id) is None:
        raise KeyError(f"no household member with id {member_id!r}")
