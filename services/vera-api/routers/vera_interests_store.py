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
# Legacy watch columns: standing watches now live in Vera's journal (routers/journal.py); these
# columns remain only so `active()` works on every store vintage and so the journal's one-time
# migration can read any rows that predate it.
_WATCH_COLUMNS = [
    ("is_watch", "INTEGER DEFAULT 0"),   # 1 = legacy watch row (excluded from the curiosity-explore set)
    ("watch_query", "TEXT"),
    ("metric", "TEXT"),
    ("last_finding", "TEXT"),
    ("last_value", "REAL"),
    ("last_checked", "INTEGER"),
    ("last_changed_at", "INTEGER"),
    ("origin", "TEXT"),
    ("expire_at", "INTEGER"),
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


def _cutover():
    """True once the Profile Graph is the interest write target: legacy interest ACCRUAL stops
    so this deprecated store no longer diverges. Reversible via PROFILE_GRAPH_CUTOVER; reads and
    cooldown bookkeeping are unaffected."""
    return os.environ.get("PROFILE_GRAPH_CUTOVER", "").strip().lower() not in ("", "0", "false", "no")


def observe(topic, stance=None, salience_bump=1.0, source="self", provenance=None):
    """Note/strengthen one of her interests. Idempotent on topic — re-observing bumps salience so a
    recurring interest rises. Preserves an existing stance if none is given. Free."""
    if not topic or not topic.strip():
        return None
    if _cutover():
        return None   # the Profile Graph is the write target now; the legacy store stops accruing
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


def cooled(topics, now=None):
    """The subset of `topics` currently on a fixation cooldown — what a proposing loop
    (pulse triage, the for-you tick) must withhold so it rotates instead of replaying."""
    if not topics:
        return set()
    init()
    now = now if now is not None else int(time.time())
    out = set()
    with _conn() as c:
        for t in topics:
            r = c.execute("SELECT cooldown_until FROM interest WHERE id=?", (_iid(t),)).fetchone()
            if r and r["cooldown_until"] and r["cooldown_until"] > now:
                out.add(t)
    return out


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
