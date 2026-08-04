import logging
import time

from . import pulse_store
from . import user_profile_store as up
from .identity import owner_id

log = logging.getLogger("vera.identity")

MARKER = "native_identity_v1"


def _marked(c) -> bool:
    c.execute("CREATE TABLE IF NOT EXISTS migration (name TEXT PRIMARY KEY, applied_at INTEGER)")
    return c.execute("SELECT 1 FROM migration WHERE name=?", (MARKER,)).fetchone() is not None


def _stamp(c):
    c.execute("INSERT OR IGNORE INTO migration(name, applied_at) VALUES(?, ?)",
              (MARKER, int(time.time())))


def _migrate_pulse(oid: str) -> dict:
    pulse_store.init()
    with pulse_store._conn() as c:
        if _marked(c):
            return {"skipped": True}
        cards = c.execute(
            "UPDATE cards SET user_id=? WHERE user_id IS NULL OR user_id != ?",
            (oid, oid)).rowcount
        c.execute("UPDATE OR IGNORE pulse_reads SET user_id=? WHERE user_id != ?", (oid, oid))
        dropped = c.execute("DELETE FROM pulse_reads WHERE user_id != ?", (oid,)).rowcount
        _stamp(c)
        return {"cards": cards, "reads_dropped": dropped}


def _merge_profiles(c, oid: str, rows) -> None:
    merged = {"name": None, "persona": None, "prefs": None, "updated_at": 0}
    for r in sorted((dict(x) for x in rows), key=lambda x: x.get("updated_at") or 0):
        for k in ("name", "persona", "prefs"):
            if r.get(k):
                merged[k] = r[k]
        merged["updated_at"] = max(merged["updated_at"], r.get("updated_at") or 0)
    c.execute("DELETE FROM profile WHERE user_id != ?", (oid,))
    c.execute(
        """INSERT INTO profile(user_id,name,persona,prefs,updated_at) VALUES(?,?,?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET name=excluded.name, persona=excluded.persona,
             prefs=excluded.prefs, updated_at=excluded.updated_at""",
        (oid, merged["name"], merged["persona"], merged["prefs"],
         merged["updated_at"] or int(time.time())))


def _migrate_interests(c, oid: str) -> int:
    moved = 0
    for r in c.execute("SELECT * FROM interest WHERE user_id != ?", (oid,)).fetchall():
        nid = up._iid(oid, r["topic"])
        ex = c.execute("SELECT weight FROM interest WHERE id=?", (nid,)).fetchone()
        if ex:
            c.execute(
                "UPDATE interest SET weight=?, gloss=COALESCE(gloss, ?), updated_at=? WHERE id=?",
                (max(ex["weight"] or 0.0, r["weight"] or 0.0), r["gloss"],
                 int(time.time()), nid))
            c.execute("DELETE FROM interest WHERE id=?", (r["id"],))
        else:
            c.execute("UPDATE interest SET id=?, user_id=? WHERE id=?", (nid, oid, r["id"]))
        moved += 1
    return moved


def _migrate_profile_store(oid: str) -> dict:
    up.init()
    with up._conn() as c:
        if _marked(c):
            return {"skipped": True}
        rows = c.execute("SELECT * FROM profile").fetchall()
        if any(r["user_id"] != oid for r in rows):
            _merge_profiles(c, oid, rows)
        interests = _migrate_interests(c, oid)
        _stamp(c)
        return {"profiles": len(rows), "interests": interests}


def run() -> dict:
    oid = owner_id()
    out = {"owner_id": oid}
    try:
        out["pulse"] = _migrate_pulse(oid)
    except Exception as e:
        log.warning("identity migration (pulse store) failed: %s", e)
        out["pulse"] = {"error": str(e)}
    try:
        out["profiles"] = _migrate_profile_store(oid)
    except Exception as e:
        log.warning("identity migration (profile store) failed: %s", e)
        out["profiles"] = {"error": str(e)}
    return out
