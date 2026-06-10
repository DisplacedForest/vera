"""Media decisions store — every media rec the user acted on, so the weekly curation
never re-proposes it. A skipped title must never resurface; an approved one isn't re-suggested.

Keyed by (media_type, tmdb_id). `reason` is "skipped" | "approved". Pure SQLite, unit-testable.
"""
import os
import sqlite3
import time

DB_PATH = os.environ.get("MEDIA_DB_PATH", "/data/media.db")


def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS media_decisions (
                media_type TEXT,
                tmdb_id INTEGER,
                title TEXT,
                reason TEXT,          -- "skipped" | "approved"
                decided_at INTEGER,
                PRIMARY KEY (media_type, tmdb_id)
            )
            """
        )


def record(media_type: str, tmdb_id: int, title: str, reason: str):
    """Remember a decision. INSERT OR REPLACE — the latest decision for a title wins (idempotent)."""
    init()
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO media_decisions(media_type, tmdb_id, title, reason, decided_at) "
            "VALUES (?,?,?,?,?)",
            (media_type, int(tmdb_id), title or "", reason, int(time.time())),
        )


def seen(media_type: str, tmdb_id: int) -> bool:
    init()
    with _conn() as c:
        r = c.execute(
            "SELECT 1 FROM media_decisions WHERE media_type=? AND tmdb_id=?",
            (media_type, int(tmdb_id)),
        ).fetchone()
    return r is not None


def seen_keys() -> set:
    """Every (media_type, tmdb_id) the user has acted on — the curation filter set."""
    init()
    with _conn() as c:
        rows = c.execute("SELECT media_type, tmdb_id FROM media_decisions").fetchall()
    return {(r["media_type"], r["tmdb_id"]) for r in rows}


def all() -> list[dict]:
    init()
    with _conn() as c:
        rows = c.execute(
            "SELECT media_type, tmdb_id, title, reason, decided_at FROM media_decisions "
            "ORDER BY decided_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]
