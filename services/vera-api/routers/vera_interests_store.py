"""Vera's OWN emergent interests — her "desire", not hardcoded seeds.

Distinct from `user_profile_store` (interests she follows for a PERSON). These are HER interests,
emerging from:
  - 'fact-cluster' — clusters of her grounded world-model facts (what she keeps learning about),
  - 'self'         — topics she actually chooses to explore on a tick (accrue as she returns),
  - 'chat'         — what the owner engages her on (folded in from the per-user interest store).

Each tick reads the ACTIVE (non-cooled-down) interests to decide what to explore, then `touch()`es
the chosen topic onto a fixation cooldown — the anti-fixation mechanism that makes her range widely
instead of returning to one favourite topic tick after tick. Salience accrues; Dreaming refreshes
nightly. Writes are free.
"""
import difflib
import hashlib
import json
import os
import sqlite3
import time

DB_PATH = os.environ.get("VERA_INTERESTS_DB_PATH", "/data/vera_interests/store.db")
COOLDOWN_HOURS = int(os.environ.get("VERA_INTEREST_COOLDOWN_HOURS", "48"))


def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


# Extra columns added after the original schema shipped — created on existing DBs via ALTER (below).
# A "watch" is a standing question Vera re-asks until it resolves: a thing to monitor, re-checked on
# her heartbeat, surfacing a card only when its answer materially changes.
_WATCH_COLUMNS = [
    ("is_watch", "INTEGER DEFAULT 0"),   # 1 = monitored watch (excluded from the curiosity-explore set)
    ("watch_query", "TEXT"),             # the search to re-run each check
    ("metric", "TEXT"),                  # optional numeric hint (e.g. "USD/ton") → enables a delta test
    ("last_finding", "TEXT"),            # the last grounded finding (the baseline to compare against)
    ("last_value", "REAL"),              # the last extracted number, when metric is set
    ("last_checked", "INTEGER"),
    ("last_changed_at", "INTEGER"),
    ("origin", "TEXT"),                  # the situation/headline that spawned the watch
    ("expire_at", "INTEGER"),            # auto-retire when the situation goes stale
]


def init():
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS interest (
                id TEXT PRIMARY KEY,        -- hash(topic)
                topic TEXT,
                stance TEXT,                -- her take / why she cares (optional, set by Dreaming)
                salience REAL,              -- accrues as she returns to / learns the topic
                source TEXT,                -- 'fact-cluster' | 'self' | 'chat' | 'watch'
                times_explored INTEGER,     -- how often a tick has chosen it
                last_explored INTEGER,
                cooldown_until INTEGER,     -- hidden from selection until past this (anti-fixation)
                provenance TEXT,
                learned_at INTEGER,
                updated_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_interest_salience ON interest(salience);
            CREATE INDEX IF NOT EXISTS idx_interest_cooldown ON interest(cooldown_until);
            """
        )
        cols = {r["name"] for r in c.execute("PRAGMA table_info(interest)").fetchall()}
        for name, decl in _WATCH_COLUMNS:
            if name not in cols:
                c.execute(f"ALTER TABLE interest ADD COLUMN {name} {decl}")


def _iid(topic):
    return hashlib.sha1(topic.strip().lower().encode()).hexdigest()[:12]


def observe(topic, stance=None, salience_bump=1.0, source="self", provenance=None):
    """Note/strengthen one of her interests. Idempotent on topic — re-observing bumps salience so a
    recurring interest rises. Preserves an existing stance if none is given. Free."""
    if not topic or not topic.strip():
        return None
    init()
    iid = _iid(topic)
    now = int(time.time())
    with _conn() as c:
        row = c.execute("SELECT salience, learned_at FROM interest WHERE id=?", (iid,)).fetchone()
        salience = (row["salience"] if row else 0.0) + salience_bump
        learned = row["learned_at"] if row else now
        c.execute(
            """INSERT INTO interest(id,topic,stance,salience,source,times_explored,last_explored,
                                    cooldown_until,provenance,learned_at,updated_at)
               VALUES(?,?,?,?,?,0,NULL,NULL,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 salience=excluded.salience, source=excluded.source,
                 stance=COALESCE(excluded.stance, interest.stance),
                 provenance=COALESCE(excluded.provenance, interest.provenance),
                 updated_at=excluded.updated_at""",
            (iid, topic.strip(), stance, salience, source,
             json.dumps(provenance) if provenance is not None else None, learned, now),
        )
    return iid


def touch(topic, cooldown_hours=COOLDOWN_HOURS):
    """Mark a topic as just-explored: bump times_explored and put it on a fixation cooldown so the
    next ticks won't re-pick it. Returns the cooldown expiry."""
    init()
    now = int(time.time())
    until = now + cooldown_hours * 3600
    iid = _iid(topic)
    with _conn() as c:
        c.execute(
            "UPDATE interest SET times_explored=COALESCE(times_explored,0)+1, last_explored=?, "
            "cooldown_until=?, updated_at=? WHERE id=?", (now, until, now, iid))
    return until


def _row(r):
    return {"id": r["id"], "topic": r["topic"], "stance": r["stance"], "salience": r["salience"],
            "source": r["source"], "times_explored": r["times_explored"] or 0,
            "last_explored": r["last_explored"], "cooldown_until": r["cooldown_until"]}


def active(limit=20, now=None):
    """Interests available to explore right now — those NOT on cooldown — ranked by salience tempered
    by novelty (salience / (1 + times_explored)), so a fresh interest outranks an over-worked one."""
    init()
    now = now if now is not None else int(time.time())
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM interest WHERE COALESCE(is_watch,0)=0 "
            "AND (cooldown_until IS NULL OR cooldown_until <= ?) "
            "ORDER BY (salience / (1.0 + COALESCE(times_explored,0))) DESC, updated_at DESC LIMIT ?",
            (now, limit)).fetchall()
    return [_row(r) for r in rows]


def all_interests(limit=200):
    init()
    with _conn() as c:
        rows = c.execute("SELECT * FROM interest ORDER BY salience DESC, updated_at DESC LIMIT ?",
                         (limit,)).fetchall()
    return [_row(r) for r in rows]


def set_stance(topic, stance):
    init()
    with _conn() as c:
        c.execute("UPDATE interest SET stance=?, updated_at=? WHERE id=?",
                  (stance, int(time.time()), _iid(topic)))


def delete(interest_id):
    init()
    with _conn() as c:
        c.execute("DELETE FROM interest WHERE id=?", (interest_id,))


# --------------------------------------------------------------------------- watches

def _watch_row(r):
    return {"id": r["id"], "topic": r["topic"], "watch_query": r["watch_query"], "metric": r["metric"],
            "last_finding": r["last_finding"], "last_value": r["last_value"],
            "last_checked": r["last_checked"], "origin": r["origin"], "expire_at": r["expire_at"]}


def add_watch(topic, query, metric=None, origin=None, ttl_days=14, now=None):
    """Register a standing watch: a thing to monitor + the search to re-run. Idempotent on topic —
    re-adding refreshes the query/expiry but PRESERVES the last finding so monitoring continuity
    survives. Returns the interest id, or None if topic/query is empty."""
    if not (topic and topic.strip() and query and query.strip()):
        return None
    init()
    iid = _iid(topic)
    now = now if now is not None else int(time.time())
    expire = now + int(ttl_days) * 86400
    with _conn() as c:
        row = c.execute("SELECT learned_at FROM interest WHERE id=?", (iid,)).fetchone()
        learned = row["learned_at"] if row else now
        c.execute(
            """INSERT INTO interest(id,topic,stance,salience,source,times_explored,last_explored,
                                    cooldown_until,provenance,learned_at,updated_at,
                                    is_watch,watch_query,metric,origin,expire_at)
               VALUES(?,?,NULL,?,?,0,NULL,NULL,NULL,?,?,1,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 is_watch=1, watch_query=excluded.watch_query, metric=excluded.metric,
                 origin=COALESCE(excluded.origin, interest.origin),
                 expire_at=excluded.expire_at, updated_at=excluded.updated_at""",
            (iid, topic.strip(), 0.1, "watch", learned, now, query.strip(), metric, origin, expire))
    return iid


def due_watches(now=None, stale_after_hours=24, limit=5):
    """Active, non-expired watches not checked within `stale_after_hours` — never-checked first, then
    oldest-checked. `limit` spreads load across heartbeat ticks."""
    init()
    now = now if now is not None else int(time.time())
    cutoff = now - int(stale_after_hours) * 3600
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM interest WHERE COALESCE(is_watch,0)=1 "
            "AND (expire_at IS NULL OR expire_at > ?) "
            "AND (last_checked IS NULL OR last_checked <= ?) "
            "ORDER BY last_checked IS NOT NULL, last_checked ASC LIMIT ?",
            (now, cutoff, limit)).fetchall()
    return [_watch_row(r) for r in rows]


def record_watch_check(interest_id, finding=None, value=None, changed=False, now=None):
    """Record a re-check. Always stamps last_checked. Writes last_finding/last_value only when given
    (caller passes them to set the baseline or on a detected change), so unchanged re-checks compare
    against the last KNOWN state rather than resetting it."""
    init()
    now = now if now is not None else int(time.time())
    sets, args = ["last_checked=?"], [now]
    if finding is not None:
        sets.append("last_finding=?"); args.append(finding)
    if value is not None:
        sets.append("last_value=?"); args.append(value)
    if changed:
        sets.append("last_changed_at=?"); args.append(now)
    sets.append("updated_at=?"); args.append(now)
    args.append(interest_id)
    with _conn() as c:
        c.execute(f"UPDATE interest SET {', '.join(sets)} WHERE id=?", args)


def purge_expired_watches(now=None):
    """Drop watches whose situation has gone stale (past expire_at). Returns the count removed."""
    init()
    now = now if now is not None else int(time.time())
    with _conn() as c:
        cur = c.execute("DELETE FROM interest WHERE COALESCE(is_watch,0)=1 "
                        "AND expire_at IS NOT NULL AND expire_at <= ?", (now,))
        return cur.rowcount


def _norm(f):
    return f"{f.get('topic') or ''} {f.get('content') or ''}".strip().lower()


def derive_from_facts(facts, threshold=0.5, min_cluster=2):
    """Emergent fact-cluster interests: greedily cluster grounded facts by similarity; each cluster of
    >= min_cluster facts contributes an interest (its shortest topic), with salience proportional to
    the cluster size. Mechanical/idempotent — re-running keeps interests in step with her world-model.
    Returns the topics observed."""
    clusters = []
    for f in facts:
        nf = _norm(f)
        placed = False
        for cl in clusters:
            if difflib.SequenceMatcher(None, nf, cl["_n"]).ratio() >= threshold:
                cl["items"].append(f)
                placed = True
                break
        if not placed:
            clusters.append({"_n": nf, "items": [f]})
    observed = []
    for cl in clusters:
        items = cl["items"]
        if len(items) < min_cluster:
            continue
        topics = [i.get("topic") for i in items if i.get("topic")]
        if not topics:
            continue
        label = min(topics, key=len)  # the tightest label for the cluster
        observe(label, salience_bump=float(len(items)), source="fact-cluster",
                provenance={"cluster_size": len(items)})
        observed.append(label)
    return observed
