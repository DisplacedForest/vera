"""Per-user profile store — the "personal vibe" half of shared-mind / personal-vibe.

Vera has ONE shared world-model (vera_memory) and one home (knowledge_store). This store is the
opposite: keyed by OWUI user id, it holds what is personal to each person — her relationship with
them. It starts empty and accrues from patterns (the "goals start non-existent" stance).

  - persona:   free-text notes on how Vera relates to this person (tone, what they're like).
  - interests: weighted topics she pursues on THEIR behalf (heartbeat + Pulse ground in these).
  - prefs:     per-user Pulse/behaviour preferences (json).

The digest (persona + top interests) is injected per-user by the vera_memory inlet filter, on top
of the shared world-model core. Writes are free — it's about the person, no external effect.
Mirrors the vera_memory_store / knowledge_store SQLite pattern.
"""
import hashlib
import json
import os
import sqlite3
import time

DB_PATH = os.environ.get("USER_PROFILE_DB_PATH", "/data/user_profiles/store.db")


def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS profile (
                user_id TEXT PRIMARY KEY,
                name TEXT,
                persona TEXT,
                prefs TEXT,            -- json
                updated_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS interest (
                id TEXT PRIMARY KEY,   -- hash(user_id, topic)
                user_id TEXT,
                topic TEXT,
                weight REAL,           -- accrues as she observes the interest again
                source TEXT,
                provenance TEXT,       -- json
                learned_at INTEGER,
                updated_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_interest_user ON interest(user_id);
            """
        )
        # `gloss` is a one-line meaning per interest so matching is semantic, not lexical
        # (so "Wine-OS" software never clears the "winemaking" interest on the shared word "wine").
        cols = [r[1] for r in c.execute("PRAGMA table_info(interest)").fetchall()]
        if "gloss" not in cols:
            c.execute("ALTER TABLE interest ADD COLUMN gloss TEXT")


def _iid(user_id, topic):
    return hashlib.sha1(f"{user_id}\n{topic.strip().lower()}".encode()).hexdigest()[:12]


def set_persona(user_id, name=None, persona=None, prefs=None):
    init()
    now = int(time.time())
    with _conn() as c:
        row = c.execute("SELECT name, persona, prefs FROM profile WHERE user_id=?", (user_id,)).fetchone()
        c.execute(
            """INSERT INTO profile(user_id,name,persona,prefs,updated_at) VALUES(?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET name=excluded.name, persona=excluded.persona,
                 prefs=excluded.prefs, updated_at=excluded.updated_at""",
            (user_id,
             name if name is not None else (row["name"] if row else None),
             persona if persona is not None else (row["persona"] if row else None),
             json.dumps(prefs) if prefs is not None else (row["prefs"] if row else None),
             now),
        )


def observe(user_id, topic, weight=1.0, source="vera", provenance=None, gloss=None):
    """Note an interest for this person. Idempotent on (user_id, topic) — re-observing bumps weight,
    so a recurring interest rises. `gloss` is an optional one-line meaning; re-observing
    without one preserves any existing gloss. Free."""
    init()
    if not topic or not topic.strip():
        return None
    iid = _iid(user_id, topic)
    now = int(time.time())
    with _conn() as c:
        row = c.execute("SELECT weight, learned_at FROM interest WHERE id=?", (iid,)).fetchone()
        new_w = (row["weight"] if row else 0.0) + weight
        learned = row["learned_at"] if row else now
        c.execute(
            """INSERT INTO interest(id,user_id,topic,weight,source,provenance,gloss,learned_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET weight=excluded.weight, source=excluded.source,
                 provenance=excluded.provenance, gloss=COALESCE(excluded.gloss, interest.gloss),
                 updated_at=excluded.updated_at""",
            (iid, user_id, topic.strip(), new_w, source,
             json.dumps(provenance) if provenance is not None else None, gloss, learned, now),
        )
    return iid


def set_gloss(user_id, topic, gloss):
    """Attach/replace the one-line meaning for an interest. Used by the heartbeat's lazy
    gloss backfill so the relevance gate has real meaning to test candidates against."""
    init()
    with _conn() as c:
        c.execute("UPDATE interest SET gloss=?, updated_at=? WHERE id=?",
                  (gloss, int(time.time()), _iid(user_id, topic)))


def remove_interest(interest_id):
    init()
    with _conn() as c:
        c.execute("DELETE FROM interest WHERE id=?", (interest_id,))


def interests(user_id, limit=50):
    init()
    with _conn() as c:
        rows = c.execute(
            "SELECT id, topic, weight, gloss FROM interest WHERE user_id=? ORDER BY weight DESC, updated_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [{"id": r["id"], "topic": r["topic"], "weight": r["weight"], "gloss": r["gloss"]} for r in rows]


def get(user_id):
    init()
    with _conn() as c:
        r = c.execute("SELECT name, persona, prefs FROM profile WHERE user_id=?", (user_id,)).fetchone()
    return {
        "user_id": user_id,
        "name": r["name"] if r else None,
        "persona": r["persona"] if r else None,
        "prefs": json.loads(r["prefs"]) if (r and r["prefs"]) else {},
        "interests": interests(user_id),
    }


def digest(user_id, top=12):
    """Compact per-user vibe injected by the inlet filter (on top of the shared world-model core)."""
    p = get(user_id)
    lines = []
    if p["name"]:
        lines.append(f"You are speaking with {p['name']}.")
    if p["persona"]:
        lines.append(p["persona"].strip())
    its = [i["topic"] for i in p["interests"][:top]]
    if its:
        lines.append("Their interests you follow: " + ", ".join(its) + ".")
    return "\n".join(lines)
