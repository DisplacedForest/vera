import os
import sqlite3
import time

DB_PATH = os.environ.get("VEIN_ENGINE_DB_PATH", "/data/vein_engine.db")
DECAY_DAYS = int(os.environ.get("VEIN_SEEN_DECAY_DAYS", "7"))


def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init():
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS seen (
                kind     TEXT,
                key      TEXT,
                first_ts INTEGER,
                last_ts  INTEGER,
                PRIMARY KEY (kind, key)
            )
            """
        )
        c.execute("CREATE TABLE IF NOT EXISTS floor (kind TEXT PRIMARY KEY, last_run_ts INTEGER)")


def default_window_secs() -> int:
    return DECAY_DAYS * 86400


def record_seen(kind: str, keys: list[str], ts: int | None = None) -> None:
    if not keys:
        return
    init()
    now = int(ts if ts is not None else time.time())
    with _conn() as c:
        for k in keys:
            c.execute(
                """
                INSERT INTO seen (kind, key, first_ts, last_ts) VALUES (?, ?, ?, ?)
                ON CONFLICT (kind, key) DO UPDATE SET last_ts = excluded.last_ts
                """,
                (kind, k, now, now))
        c.execute("DELETE FROM seen WHERE last_ts < ?",
                  (int(time.time()) - default_window_secs(),))


def filter_unseen(kind: str, keys: list[str], window_secs: int | None = None) -> list[str]:
    if not keys:
        return []
    init()
    cutoff = int(time.time()) - (window_secs if window_secs is not None else default_window_secs())
    with _conn() as c:
        rows = c.execute(
            f"SELECT key FROM seen WHERE kind = ? AND last_ts >= ? AND key IN ({','.join('?' * len(keys))})",
            (kind, cutoff, *keys)).fetchall()
    seen = {r["key"] for r in rows}
    return [k for k in keys if k not in seen]


def seen_count(kind: str) -> int:
    init()
    with _conn() as c:
        return c.execute("SELECT COUNT(*) n FROM seen WHERE kind = ?", (kind,)).fetchone()["n"]


def last_run(kind: str) -> int | None:
    init()
    with _conn() as c:
        r = c.execute("SELECT last_run_ts FROM floor WHERE kind = ?", (kind,)).fetchone()
    return r["last_run_ts"] if r else None


def mark_run(kind: str, ts: int | None = None) -> None:
    init()
    with _conn() as c:
        c.execute(
            """
            INSERT INTO floor (kind, last_run_ts) VALUES (?, ?)
            ON CONFLICT (kind) DO UPDATE SET last_run_ts = excluded.last_run_ts
            """,
            (kind, int(ts if ts is not None else time.time())))
