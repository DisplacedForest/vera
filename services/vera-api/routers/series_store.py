import os
import sqlite3
import time

DB_PATH = os.environ.get("SERIES_DB_PATH", "/data/series.db")
RETAIN_DAYS = int(os.environ.get("SERIES_RETAIN_DAYS", "365"))

BACKFILL_LIMIT = 1_000_000


def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _ensure():
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS series (
                entity_id TEXT,
                ts        INTEGER,
                value     REAL,
                PRIMARY KEY (entity_id, ts)
            )
            """
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_series_entity_ts ON series(entity_id, ts)")
        c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")


def init():
    _ensure()
    with _conn() as c:
        done = c.execute("SELECT value FROM meta WHERE key = 'backfilled'").fetchone()
    if not done:
        backfill_from_events()
        with _conn() as c:
            c.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('backfilled', ?)",
                      (str(int(time.time())),))


def backfill_from_events() -> int:
    from . import home_events_store
    from .home_model_mine import _as_float
    rows = []
    for e in home_events_store.recent(limit=BACKFILL_LIMIT, event_type="state_changed"):
        if (e.get("domain") or "") != "sensor":
            continue
        v = _as_float(e.get("new_state"))
        if v is None:
            continue
        rows.append((e["entity_id"], int(e["ts"]), v))
    return insert_many(rows)


def insert(entity_id: str, ts: int, value: float) -> None:
    _ensure()
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO series (entity_id, ts, value) VALUES (?, ?, ?)",
                  (entity_id, int(ts), float(value)))


def insert_many(rows: list[tuple[str, int, float]]) -> int:
    if not rows:
        return 0
    _ensure()
    with _conn() as c:
        cur = c.executemany(
            "INSERT OR IGNORE INTO series (entity_id, ts, value) VALUES (?, ?, ?)",
            [(e, int(t), float(v)) for e, t, v in rows])
        return cur.rowcount


def series(entity_id: str, since: int | None = None, until: int | None = None,
           limit: int | None = None) -> list[tuple[int, float]]:
    _ensure()
    where, args = ["entity_id = ?"], [entity_id]
    if since is not None:
        where.append("ts >= ?")
        args.append(int(since))
    if until is not None:
        where.append("ts <= ?")
        args.append(int(until))
    sql = "SELECT ts, value FROM series WHERE " + " AND ".join(where) + " ORDER BY ts"
    if limit is not None:
        sql += " LIMIT ?"
        args.append(int(limit))
    with _conn() as c:
        return [(r["ts"], r["value"]) for r in c.execute(sql, args).fetchall()]


def entities(min_points: int = 0) -> list[dict]:
    _ensure()
    with _conn() as c:
        rows = c.execute(
            """
            SELECT entity_id, COUNT(*) n, MIN(ts) a, MAX(ts) b FROM series
            GROUP BY entity_id HAVING COUNT(*) >= ? ORDER BY entity_id
            """, (int(min_points),)).fetchall()
    return [{"entity_id": r["entity_id"], "count": r["n"],
             "first_ts": r["a"], "last_ts": r["b"]} for r in rows]


def latest(entity_id: str) -> tuple[int, float] | None:
    _ensure()
    with _conn() as c:
        r = c.execute("SELECT ts, value FROM series WHERE entity_id = ? ORDER BY ts DESC LIMIT 1",
                      (entity_id,)).fetchone()
    return (r["ts"], r["value"]) if r else None


def purge(retain_days: int | None = None) -> int:
    _ensure()
    cutoff = int(time.time()) - (retain_days or RETAIN_DAYS) * 86400
    with _conn() as c:
        return c.execute("DELETE FROM series WHERE ts < ?", (cutoff,)).rowcount
