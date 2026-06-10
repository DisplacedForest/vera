"""Rhythm store — per-entity HA usage baselines + deterministic deviation detection.

SQLite at /data/rhythm.db (mirrors pulse_store / knowledge_store). Models each entity's activity as
a 7x24 (day-of-week x hour) distribution accumulated nightly from HA history. Everything here is
either sqlite or pure math — no HA / network — so it's unit-testable in isolation. The HA I/O and
LLM narration live in home.py.
"""
import os
import sqlite3
from datetime import date, datetime, time, timedelta

DB_PATH = os.environ.get("RHYTHM_DB_PATH", "/data/rhythm.db")
DECAY_CAP = 60.0  # rolling window: per-bucket observations are capped ~60 days so old habits fade


def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS entity_meta (
                entity_id TEXT PRIMARY KEY, domain TEXT, friendly_name TEXT,
                active_def TEXT, first_seen TEXT, last_seen TEXT)"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS activity_buckets (
                entity_id TEXT, dow INTEGER, hour INTEGER,
                active_days REAL DEFAULT 0, observed_days REAL DEFAULT 0,
                PRIMARY KEY (entity_id, dow, hour))"""
        )
        c.execute("CREATE TABLE IF NOT EXISTS digests (day TEXT PRIMARY KEY, summary TEXT, deviations_json TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS ingest_log (day TEXT PRIMARY KEY)")


# ---- ingestion -------------------------------------------------------------

def record_day(day_iso: str, active_hours_by_entity: dict, meta: dict) -> bool:
    """Fold one day's observations into the baselines. Idempotent per day (ingest_log).

    active_hours_by_entity: {entity_id: set(hours 0..23 the entity was active)}.
    meta: {entity_id: {"domain","friendly_name","active_def"}}.
    Returns False if the day was already ingested.
    """
    init()
    dow = date.fromisoformat(day_iso).weekday()
    with _conn() as c:
        if c.execute("SELECT 1 FROM ingest_log WHERE day=?", (day_iso,)).fetchone():
            return False
        for eid, hours in active_hours_by_entity.items():
            m = meta.get(eid, {})
            c.execute(
                """INSERT INTO entity_meta (entity_id, domain, friendly_name, active_def, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(entity_id) DO UPDATE SET
                       last_seen=excluded.last_seen,
                       friendly_name=excluded.friendly_name,
                       active_def=excluded.active_def""",
                (eid, m.get("domain"), m.get("friendly_name"), m.get("active_def"), day_iso, day_iso),
            )
            for h in range(24):
                active = 1.0 if h in hours else 0.0
                row = c.execute(
                    "SELECT active_days, observed_days FROM activity_buckets WHERE entity_id=? AND dow=? AND hour=?",
                    (eid, dow, h),
                ).fetchone()
                if row is None:
                    c.execute(
                        "INSERT INTO activity_buckets (entity_id,dow,hour,active_days,observed_days) VALUES (?,?,?,?,?)",
                        (eid, dow, h, active, 1.0),
                    )
                else:
                    a, o = row["active_days"], row["observed_days"]
                    if o >= DECAY_CAP:
                        scale = (DECAY_CAP - 1) / DECAY_CAP
                        a *= scale
                        o *= scale
                    c.execute(
                        "UPDATE activity_buckets SET active_days=?, observed_days=? WHERE entity_id=? AND dow=? AND hour=?",
                        (a + active, o + 1.0, eid, dow, h),
                    )
        c.execute("INSERT INTO ingest_log (day) VALUES (?)", (day_iso,))
    return True


# ---- baseline queries ------------------------------------------------------

def prob(entity_id: str, dow: int, hour: int) -> tuple:
    """(P(active|dow,hour), observed_days). Unseen bucket -> (0.0, 0.0)."""
    init()
    with _conn() as c:
        r = c.execute(
            "SELECT active_days, observed_days FROM activity_buckets WHERE entity_id=? AND dow=? AND hour=?",
            (entity_id, dow, hour),
        ).fetchone()
    if not r or not r["observed_days"]:
        return (0.0, 0.0)
    return (r["active_days"] / r["observed_days"], r["observed_days"])


def baseline(entity_id: str) -> dict:
    """{(dow,hour): (p, observed_days)} for every bucket the entity has."""
    init()
    with _conn() as c:
        rows = c.execute(
            "SELECT dow, hour, active_days, observed_days FROM activity_buckets WHERE entity_id=?",
            (entity_id,),
        ).fetchall()
    out = {}
    for r in rows:
        o = r["observed_days"]
        out[(r["dow"], r["hour"])] = (r["active_days"] / o if o else 0.0, o)
    return out


# ---- deviation detection (pure) -------------------------------------------

def detect(entity_id: str, p: float, observed: float, active_now: bool,
           *, min_obs: int = 10, hi: float = 0.8, lo: float = 0.05):
    """Deterministic detector. Returns a deviation dict or None.

    Gated by a confidence floor: nothing fires until the bucket has >= min_obs observed days.
    """
    if observed < min_obs:
        return None
    if p >= hi and not active_now:
        return {"entity": entity_id, "kind": "absent", "p": round(p, 3), "observed": observed}
    if p <= lo and active_now:
        return {"entity": entity_id, "kind": "unexpected", "p": round(p, 3), "observed": observed}
    return None


# ---- active-hour extraction (pure) ----------------------------------------

def hours_active(states, is_active, tz, day_iso: str) -> set:
    """Set of hours [0..23] during day_iso (in tz) the entity held an active state.

    states: iterable of (iso_timestamp, state). A state persists from its timestamp until the next
    change. The last sample at/before midnight sets the opening state; if there is none, the entity is
    treated as inactive until its first in-window sample.
    """
    day = date.fromisoformat(day_iso)
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    pts = sorted((datetime.fromisoformat(t).astimezone(tz), s) for t, s in states)

    state_at_start = None
    boundary = []
    for t, s in pts:
        if t <= start:
            state_at_start = s
        elif t < end:
            boundary.append((t, s))

    hours = set()
    seg_start, cur = start, state_at_start
    for t, s in boundary:
        if cur is not None and is_active(cur):
            _mark(hours, seg_start, t, start)
        seg_start, cur = t, s
    if cur is not None and is_active(cur):
        _mark(hours, seg_start, end, start)
    return hours


def _mark(hours: set, a, b, day_start):
    """Mark every hour index in [0..23] overlapped by [a, b)."""
    h0 = int((a - day_start).total_seconds() // 3600)
    hb = int((b - day_start - timedelta(microseconds=1)).total_seconds() // 3600)
    for h in range(max(0, h0), min(23, hb) + 1):
        hours.add(h)


# ---- digest archive --------------------------------------------------------

def save_digest(day: str, summary: str, deviations_json: str):
    init()
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO digests (day, summary, deviations_json) VALUES (?,?,?)",
            (day, summary, deviations_json),
        )
