"""Shared grooming vocabulary — the change-set contract for the home-knowledge store.

This module is the single source of truth for:
- the *snapshot* (entity | type) and the *op* that wraps before/after,
- the *suppression* store that makes Reject durable ("don't redo this next run"),
- the *stale-snapshot* guard that stops a Restore/Reject from clobbering state a later run changed.

No FastAPI here — pure helpers the groomer and the restore/reject endpoints share. Stores are
imported lazily inside functions to avoid import cycles.
"""
import os
import sqlite3
import time

DB_PATH = os.environ.get("GROOM_DB_PATH", "/data/groom.db")


def _ks():
    try:
        from . import knowledge_store as m
    except ImportError:
        import knowledge_store as m
    return m


# --- snapshots ---------------------------------------------------------------------------------
# Every snapshot carries a `kind` discriminator so the client renders the right shape and the
# restore path knows how to reverse it.

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

def op_identity(o):
    """The identity a suppression is keyed on for op `o` — derived from its before-snapshot(s).
    Type ops key on the type name; entity ops key on the entity id."""
    before = o.get("before") or []
    after = o.get("after")
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


# --- stale-snapshot guard ----------------------------------------------------------------------

def stale_snapshot(o):
    """True if the op's target was changed by a LATER run since this op ran — so reversing it now
    would clobber newer state. A deletion-reversal (after is None) is a re-create and inherently
    safe, so it is never stale. Compares the live store record to what the groom left (`after`)."""
    after = o.get("after")
    if not after:
        return False  # forget / gc — restore re-creates; idempotent, never stale
    if after.get("kind") == "type":
        return False  # un-codify just drops the schema row — idempotent, never stale
    cur = _ks().get(after.get("id"))
    if cur is None:
        return True
    return cur.get("attrs") != after.get("attrs") or cur.get("name") != after.get("name")
