"""Vera's world-model memory — her OWN self-authored knowledge, globally injected.

The third memory layer, distinct from Adaptive Memory v3 (facts about the user) and the Home
Knowledge store (facts about the home): this is what *Vera* has learned and concluded — her
evolving world-model, kept current against her stale training cutoff.

Three tiers:
  - 'core'    -> durable, high-impact beliefs. Rendered into a capped digest the OWUI inlet filter
                injects into EVERY request (chat, Pulse, heartbeat). The "map."
  - 'archive' -> full detail, unbounded; pulled on demand via the `recall` tool.
  - 'scratch' -> ephemeral working notes (her scribble pad). TTL'd, NEVER injected; the groomer
                purges expired ones (and can promote keepers to archive/core first).

Engine is a SQLite store (robust frequent/atomic writes + tiers + TTL + recall). For human eyes +
rollback it also mirrors a legible, git-versioned MEMORY.md (a generated view; the store is source
of truth). Writes are FREE (her own knowledge, no external effect). Mirrors knowledge_store.py.
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import time

DIR = os.environ.get("VERA_MEMORY_DIR", "/data/vera_memory")
DB_PATH = os.path.join(DIR, "store.db")
MEMORY_MD = os.path.join(DIR, "MEMORY.md")
# Hot core injected on every call — keep it tight. ~4 chars/token, so ~4000 chars ≈ ~1000 tokens.
CORE_CHAR_CAP = int(os.environ.get("VERA_MEMORY_CORE_CHARS", "4000"))
# 48h (not 18) so tick-learnings in scratch outlive the ~daily Dreaming cadence even if a run is
# missed; nightly deep-sleep promotes the keepers to archive/core well before this TTL.
SCRATCH_TTL_HOURS = int(os.environ.get("VERA_MEMORY_SCRATCH_TTL_HOURS", "48"))
TIERS = ("core", "archive", "scratch")


def _conn():
    os.makedirs(DIR, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS entry (
                id TEXT PRIMARY KEY,
                topic TEXT,
                content TEXT,
                source TEXT,
                confidence REAL,
                tier TEXT,            -- 'core' | 'archive' | 'scratch'
                learned_at INTEGER,
                updated_at INTEGER,
                expire_at INTEGER,    -- set for scratch; NULL otherwise
                provenance TEXT,      -- json: where/how she learned it
                kind TEXT DEFAULT 'fact',  -- 'fact' (grounded, cited) | 'opinion' (her take)
                fact_refs TEXT        -- json array of fact ids an opinion is anchored to
            );
            CREATE INDEX IF NOT EXISTS idx_mem_tier ON entry(tier);
            CREATE INDEX IF NOT EXISTS idx_mem_topic ON entry(topic);
            """
        )
        # Migration: existing DBs predate kind/fact_refs — add them idempotently so every
        # prior belief becomes a 'fact' (any ungrounded ones get cleaned by the groomer).
        cols = {r["name"] for r in c.execute("PRAGMA table_info(entry)").fetchall()}
        if "kind" not in cols:
            c.execute("ALTER TABLE entry ADD COLUMN kind TEXT DEFAULT 'fact'")
        if "fact_refs" not in cols:
            c.execute("ALTER TABLE entry ADD COLUMN fact_refs TEXT")
        c.execute("CREATE INDEX IF NOT EXISTS idx_mem_kind ON entry(kind)")


def _eid(topic, content):
    return hashlib.sha1(f"{topic}\n{content}".encode()).hexdigest()[:12]


def write(topic, content, source="vera", confidence=0.6, tier="archive", provenance=None,
          ttl_hours=None, kind="fact", fact_refs=None):
    """Record a learning. Idempotent on (topic, content). Scratch entries get a TTL. Free.

    kind='fact' (default) is a grounded, cited belief. kind='opinion' is her own take and MUST be
    anchored to >=1 existing fact via fact_refs; an opinion with an empty or invalid
    fact_refs is rejected so a take can never masquerade as a fact."""
    init()
    kind = kind if kind in ("fact", "opinion") else "fact"
    eid = _eid(topic, content)
    now = int(time.time())
    expire_at = now + (ttl_hours or SCRATCH_TTL_HOURS) * 3600 if tier == "scratch" else None
    refs_json = None
    with _conn() as c:
        if kind == "opinion":
            refs = list(dict.fromkeys(r for r in (fact_refs or []) if r))
            if not refs:
                raise ValueError("an opinion must cite at least one fact (fact_refs is empty)")
            found = {r["id"]: r["kind"] for r in c.execute(
                f"SELECT id, kind FROM entry WHERE id IN ({','.join('?' * len(refs))})", refs).fetchall()}
            bad = [r for r in refs if found.get(r) != "fact"]
            if bad:
                raise ValueError(f"opinion fact_refs must reference existing facts; invalid: {bad}")
            refs_json = json.dumps(refs)
        row = c.execute("SELECT learned_at FROM entry WHERE id=?", (eid,)).fetchone()
        learned = row["learned_at"] if row else now
        c.execute(
            """INSERT INTO entry(id,topic,content,source,confidence,tier,learned_at,updated_at,expire_at,provenance,kind,fact_refs)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 topic=excluded.topic, content=excluded.content, source=excluded.source,
                 confidence=excluded.confidence, tier=excluded.tier, updated_at=excluded.updated_at,
                 expire_at=excluded.expire_at, provenance=excluded.provenance,
                 kind=excluded.kind, fact_refs=excluded.fact_refs""",
            (eid, topic, content, source, confidence, tier, learned, now, expire_at,
             json.dumps(provenance) if provenance is not None else None, kind, refs_json),
        )
    return eid


def _row(r):
    return {
        "id": r["id"], "topic": r["topic"], "content": r["content"], "source": r["source"],
        "confidence": r["confidence"], "tier": r["tier"],
        "learned_at": r["learned_at"], "updated_at": r["updated_at"], "expire_at": r["expire_at"],
        "provenance": json.loads(r["provenance"]) if r["provenance"] else None,
        "kind": r["kind"] or "fact",
        "fact_refs": json.loads(r["fact_refs"]) if r["fact_refs"] else None,
    }


def recall(query=None, limit=8, include_scratch=True, kind=None):
    """Search live (non-expired) memory — core first, then by confidence/recency.
    kind='fact'|'opinion' filters to that kind; None returns both."""
    init()
    now = int(time.time())
    sql = "SELECT * FROM entry WHERE (expire_at IS NULL OR expire_at > ?)"
    args = [now]
    if not include_scratch:
        sql += " AND tier != 'scratch'"
    if kind in ("fact", "opinion"):
        sql += " AND kind = ?"
        args.append(kind)
    if query:
        sql += " AND (topic LIKE ? OR content LIKE ?)"
        args += [f"%{query}%", f"%{query}%"]
    sql += " ORDER BY (tier='core') DESC, confidence DESC, updated_at DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        return [_row(r) for r in c.execute(sql, args).fetchall()]


def core():
    init()
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM entry WHERE tier='core' ORDER BY confidence DESC, updated_at DESC"
        ).fetchall()
    return [_row(r) for r in rows]


def _labels_for(ids):
    """Map entry ids -> a short label (topic, else a content snippet) for rendering anchors."""
    ids = [i for i in dict.fromkeys(ids or []) if i]
    if not ids:
        return {}
    with _conn() as c:
        rows = c.execute(
            f"SELECT id, topic, content FROM entry WHERE id IN ({','.join('?' * len(ids))})", ids
        ).fetchall()
    return {r["id"]: (r["topic"] or (r["content"] or "")[:40]) for r in rows}


def _core_line(e, labels):
    """One rendered core line. Opinions are flagged as opinion and show their anchoring facts so the
    model never re-reads a take as a fact."""
    if e["kind"] == "opinion":
        anchors = [labels.get(r) for r in (e["fact_refs"] or [])]
        anchors = [a for a in anchors if a]
        s = f"my read: {e['content']}"
        if anchors:
            s += f"  [grounded in: {'; '.join(anchors)}]"
        return s
    return e["content"] + (f"  ({e['topic']})" if e["topic"] else "")


def core_digest(char_cap=None):
    """Her world-model map, injected on every call. Renders her ENTIRE core — she decides what
    belongs there, so we don't trim her mind to save tokens. (char_cap is an optional safety valve,
    unused in normal operation.)"""
    entries = core()
    labels = _labels_for([r for e in entries if e["kind"] == "opinion" for r in (e["fact_refs"] or [])])
    lines, used = [], 0
    for e in entries:
        line = "- " + _core_line(e, labels)
        if char_cap and used + len(line) + 1 > char_cap:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def set_tier(entry_id, tier):
    init()
    now = int(time.time())
    expire_at = now + SCRATCH_TTL_HOURS * 3600 if tier == "scratch" else None
    with _conn() as c:
        c.execute("UPDATE entry SET tier=?, expire_at=?, updated_at=? WHERE id=?",
                  (tier, expire_at, now, entry_id))


def get(entry_id):
    """One belief by id (live or expired), or None. Used by the grooming stale-snapshot guard."""
    init()
    with _conn() as c:
        r = c.execute("SELECT * FROM entry WHERE id=?", (entry_id,)).fetchone()
    return _row(r) if r else None


def delete(entry_id):
    init()
    with _conn() as c:
        c.execute("DELETE FROM entry WHERE id=?", (entry_id,))


def purge_expired():
    init()
    with _conn() as c:
        cur = c.execute("DELETE FROM entry WHERE expire_at IS NOT NULL AND expire_at < ?",
                        (int(time.time()),))
        return cur.rowcount


def mirror_markdown():
    """Write the legible MEMORY.md (core map + archive index) and git-commit it. Best-effort —
    the store is source of truth; this is the human-readable, versioned view."""
    try:
        cores = core()
        with _conn() as c:
            arch = c.execute(
                "SELECT topic, content FROM entry WHERE tier='archive' ORDER BY updated_at DESC LIMIT 200"
            ).fetchall()
        out = ["# Vera — world model", "",
               "_Auto-generated from her memory store (source of truth). Core is injected on every call._", "",
               "## Core (always in context)", ""]
        _labels = _labels_for([r for e in cores if e["kind"] == "opinion" for r in (e["fact_refs"] or [])])
        out += [f"- {_core_line(e, _labels)}" for e in cores] or ["_(empty)_"]
        out += ["", "## Archive (recall on demand)", ""]
        out += [f"- **{r['topic']}** — {r['content']}" for r in arch] or ["_(empty)_"]
        os.makedirs(DIR, exist_ok=True)
        with open(MEMORY_MD, "w") as f:
            f.write("\n".join(out) + "\n")
        # git snapshot (init once; identity inline so no global config needed)
        if not os.path.isdir(os.path.join(DIR, ".git")):
            subprocess.run(["git", "-C", DIR, "init", "-q"], capture_output=True, timeout=20)
        subprocess.run(["git", "-C", DIR, "add", "MEMORY.md"], capture_output=True, timeout=20)
        subprocess.run(["git", "-C", DIR, "-c", "user.email=vera@local", "-c", "user.name=Vera",
                        "commit", "-q", "-m", f"memory snapshot {int(time.time())}"],
                       capture_output=True, timeout=20)
        return True
    except Exception:
        return False


def live_beliefs():
    """Every live (non-expired) core+archive belief, with its current tier. This is the full picture
    of her mind handed to her each grooming pass so she can decide what (if anything) to change."""
    init()
    now = int(time.time())
    with _conn() as c:
        rows = c.execute(
            "SELECT id, topic, content, tier, confidence, learned_at, kind FROM entry "
            "WHERE tier IN ('core','archive') AND (expire_at IS NULL OR expire_at > ?) "
            "ORDER BY (tier='core') DESC, learned_at DESC", (now,)
        ).fetchall()
    return [{"id": r["id"], "topic": r["topic"], "content": r["content"],
             "tier": r["tier"], "confidence": r["confidence"], "kind": r["kind"] or "fact"} for r in rows]


def delete_ids(ids):
    """Hard-delete the given entries. Returns count deleted. (Every pass git-snapshots MEMORY.md
    first, so a removal is always recoverable from history.)"""
    init()
    ids = list(dict.fromkeys(ids or []))
    if not ids:
        return 0
    with _conn() as c:
        cur = c.execute(f"DELETE FROM entry WHERE id IN ({','.join('?' * len(ids))})", ids)
        return cur.rowcount


def groom(core_max=50, expire_archive_days=0):
    """Mechanical consolidation (the agent does judgment promotions via set_tier separately):
      - purge expired scratch,
      - enforce the core cap (demote lowest-ranked core beyond core_max to archive),
      - optionally expire archive older than N days (0 = never),
      - mirror the legible MEMORY.md.
    Returns counts."""
    init()
    purged = purge_expired()
    demoted = expired = 0
    now = int(time.time())
    with _conn() as c:
        for r in c.execute("SELECT id FROM entry WHERE tier='core' "
                           "ORDER BY confidence DESC, updated_at DESC").fetchall()[core_max:]:
            c.execute("UPDATE entry SET tier='archive', updated_at=? WHERE id=?", (now, r["id"]))
            demoted += 1
        if expire_archive_days > 0:
            cutoff = now - expire_archive_days * 86400
            cur = c.execute("DELETE FROM entry WHERE tier='archive' AND updated_at < ?", (cutoff,))
            expired = cur.rowcount
    mirror_markdown()
    return {"purged_scratch": purged, "demoted": demoted, "expired_archive": expired, "core_count": len(core())}
