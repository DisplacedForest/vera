"""Shared grooming vocabulary — the one change-set contract across both stores.

Vera edits her own knowledge overnight in two stores — the vera-memory world-model (beliefs:
topic/content/tier) and the home-knowledge store (entities: type/name/attrs, plus codified type
schemas). Without one shared change-set shape, the audit UI's change-set view — written for
beliefs — would render blanks for entity/type ops and the "Restore" button would hit the
wrong store.

This module is the single source of truth for:
- the polymorphic *snapshot* (belief | entity | type) and the *op* that wraps before/after,
- the *suppression* store that makes Reject durable ("don't redo this next run"),
- the *stale-snapshot* guard that stops a Restore/Reject from clobbering state a later run changed.

No FastAPI here — pure helpers the groomers, the session orchestrator, and the restore/reject
endpoints all share. Stores are imported lazily inside functions to avoid import cycles.
"""
import hashlib
import os
import sqlite3
import time

DB_PATH = os.environ.get("GROOM_DB_PATH", "/data/groom.db")


def _vm():
    """vera_memory_store, importable both as a package (`routers.`) and top-level (tests)."""
    try:
        from . import vera_memory_store as m
    except ImportError:
        import vera_memory_store as m
    return m


def _ks():
    try:
        from . import knowledge_store as m
    except ImportError:
        import knowledge_store as m
    return m


# --- snapshots ---------------------------------------------------------------------------------
# Every snapshot carries a `kind` discriminator so the client renders the right shape and the
# restore path knows how to reverse it.

def snap_belief(e):
    """A vera-memory belief snapshot (the pre-/post-change state of one belief)."""
    return {"kind": "belief", "id": e["id"], "topic": e.get("topic"), "content": e.get("content"),
            "tier": e.get("tier"), "confidence": e.get("confidence")}


def snap_entity(e):
    """A knowledge-store entity snapshot (full attrs so a GC'd entity can be re-created)."""
    return {"kind": "entity", "id": e["id"], "type": e.get("type"), "name": e.get("name"),
            "attrs": e.get("attrs") or {}}


def snap_type(type_, schema, migrated):
    """A knowledge-store type-codification snapshot. `migrated` is the list of entities that the
    codified schema now governs (id+name), so the card can show exactly what was affected."""
    mig = [{"id": m["id"], "name": m.get("name")} for m in (migrated or [])]
    return {"kind": "type", "type": type_, "schema": schema,
            "entity_count": len(mig), "migrated": mig}


def op(type_, store, reason, run_id=None, before=None, after=None):
    """One reversible change. `store` routes Restore/Reject; `run_id` ties a night's ops together."""
    return {"type": type_, "store": store, "reason": reason, "run_id": run_id,
            "before": before or [], "after": after}


# --- identity (for suppression keys) -----------------------------------------------------------

def _belief_identity(snap):
    """Stable identity for a belief independent of its db id (which changes across re-writes):
    a hash of topic+content, so rejecting a merge/promote suppresses the same belief next run."""
    raw = f"{(snap.get('topic') or '').strip()}|{(snap.get('content') or '').strip()}".lower()
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def op_identity(o):
    """The identity a suppression is keyed on for op `o` — derived from its before-snapshot(s)."""
    before = o.get("before") or []
    after = o.get("after")
    if o.get("store", "memory") == "memory":
        # suppress by the source belief(s) the op acted on
        parts = sorted(_belief_identity(b) for b in before) or ([_belief_identity(after)] if after else [])
        return "+".join(parts)
    # knowledge: type ops key on the type name; entity ops key on the entity id
    if o["type"] in ("promote", "codify") and after:
        return after.get("type") or ""
    return "+".join(sorted(b.get("id", "") for b in before)) or (after.get("id", "") if after else "")


# --- suppression store (durable Reject) --------------------------------------------------------

def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS groom_suppressions (
                   key TEXT PRIMARY KEY, store TEXT, op_type TEXT, identity TEXT,
                   reason TEXT, created_at INTEGER )"""
        )


def _key(store, op_type, identity):
    return f"{store}:{op_type}:{identity}"


def suppress(store, op_type, identity, reason=""):
    """Record that `op_type` on `identity` was rejected — the next groom run must not repeat it.
    Idempotent (keyed). Returns the key."""
    init()
    k = _key(store, op_type, identity)
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO groom_suppressions VALUES(?,?,?,?,?,?)",
                  (k, store, op_type, identity, reason, int(time.time())))
    return k


def is_suppressed(store, op_type, identity):
    """True if (store, op_type, identity) was previously rejected."""
    init()
    with _conn() as c:
        return c.execute("SELECT 1 FROM groom_suppressions WHERE key=?",
                         (_key(store, op_type, identity),)).fetchone() is not None


def belief_identity(topic, content):
    """Public helper so the memory groomer can test a candidate belief before acting."""
    return _belief_identity({"topic": topic, "content": content})


# --- stale-snapshot guard ----------------------------------------------------------------------

def stale_snapshot(o):
    """True if the op's target was changed by a LATER run since this op ran — so reversing it now
    would clobber newer state. A deletion-reversal (after is None) is a re-create and inherently
    safe, so it is never stale. Compares the live store record to what the groom left (`after`)."""
    after = o.get("after")
    if not after:
        return False  # forget / gc — restore re-creates; idempotent, never stale
    if o.get("store", "memory") == "memory":
        cur = _vm().get(after.get("id"))
        if cur is None:
            return True  # the belief we'd flip/un-merge is gone — a later run removed it
        return cur.get("content") != after.get("content") or cur.get("tier") != after.get("tier")
    # knowledge
    if after.get("kind") == "type":
        return False  # un-codify just drops the schema row — idempotent, never stale
    cur = _ks().get(after.get("id"))
    if cur is None:
        return True
    return cur.get("attrs") != after.get("attrs") or cur.get("name") != after.get("name")
