"""Home Knowledge store — SQLite single source of truth for durable home facts.

Flexible entity+attrs core, append-only revision log (audit / provenance / rollback), and a
type_schema promotion registry (data-layer self-authoring). Writes ONLY go through
propose() -> commit(token): preview-gated and idempotent (the token is a content hash, so an
identical proposal dedupes and a replayed commit is a no-op). Mirrors pulse_store.py.
"""

import hashlib
import json
import os
import re
import sqlite3
import time
from collections import Counter, defaultdict

DB_PATH = os.environ.get("KNOWLEDGE_DB_PATH", "/data/knowledge.db")


def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS entity (
                id TEXT PRIMARY KEY, type TEXT, name TEXT, attrs TEXT,
                created_at INTEGER, updated_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS revision (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, entity_id TEXT, type TEXT,
                op TEXT, attr TEXT, old_value TEXT, new_value TEXT,
                source TEXT, actor TEXT, chat_id TEXT, message_id TEXT, token TEXT
            );
            CREATE TABLE IF NOT EXISTS type_schema (
                type TEXT PRIMARY KEY, json_schema TEXT, version INTEGER,
                promoted_at INTEGER, promoted_by TEXT
            );
            CREATE TABLE IF NOT EXISTS pending (
                token TEXT PRIMARY KEY, created_at INTEGER, op TEXT, entity_id TEXT,
                payload TEXT, preview TEXT, status TEXT, result TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_entity_type ON entity(type);
            CREATE INDEX IF NOT EXISTS idx_rev_entity ON revision(entity_id);
            """
        )


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def _eid(type: str, name: str) -> str:
    return f"{type}:{_slug(name)}"


def _get_entity(c, eid):
    r = c.execute("SELECT * FROM entity WHERE id=?", (eid,)).fetchone()
    return dict(r) if r else None


def propose(op, entity_id=None, type=None, name=None, attrs=None,
            source="chat", actor="vera", chat_id=None, message_id=None):
    """Stage a write. Returns a human preview + a content-hash token; nothing is written yet."""
    init()
    eid = entity_id or _eid(type, name)
    attrs = attrs or {}
    with _conn() as c:
        cur = _get_entity(c, eid)
    cur_attrs = json.loads(cur["attrs"]) if cur else {}

    if op == "set":
        diff = [
            {"attr": k, "old": cur_attrs.get(k), "new": v}
            for k, v in attrs.items()
            if cur_attrs.get(k) != v
        ]
        verb = "update" if cur else "create"
        label = name or (cur or {}).get("name") or eid
        changes = "; ".join(f"{d['attr']} {d['old']!r}->{d['new']!r}" for d in diff) or "(no changes)"
        preview = f"Will {verb} {type or (cur or {}).get('type')} - {label}: {changes}"
    elif op == "delete":
        diff = []
        preview = f"Will DELETE {eid}" + ("" if cur else " (does not exist)")
    else:
        raise ValueError(f"unknown op {op}")

    payload = {
        "op": op, "entity_id": eid,
        "type": type or (cur or {}).get("type"),
        "name": name or (cur or {}).get("name"),
        "attrs": attrs, "source": source, "actor": actor,
        "chat_id": chat_id, "message_id": message_id,
    }
    token = hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:8]
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO pending VALUES(?,?,?,?,?,?,?,?)",
            (token, int(time.time()), op, eid, json.dumps(payload), preview, "pending", None),
        )
    return {"token": token, "preview": preview, "diff": diff,
            "exists": cur is not None, "entity_id": eid}


def commit(token):
    """Apply a staged write. Idempotent: replaying an applied token returns the stored result."""
    init()
    with _conn() as c:
        row = c.execute("SELECT * FROM pending WHERE token=?", (token,)).fetchone()
        if not row:
            return {"ok": False, "error": "unknown or expired token", "applied": False}
        if row["status"] == "applied":
            return {**json.loads(row["result"]), "applied": False}

        p = json.loads(row["payload"])
        now = int(time.time())
        eid = p["entity_id"]
        cur = _get_entity(c, eid)
        cur_attrs = json.loads(cur["attrs"]) if cur else {}
        revs = []

        def log(attr, old, new, op):
            c.execute(
                """INSERT INTO revision(ts,entity_id,type,op,attr,old_value,new_value,
                   source,actor,chat_id,message_id,token) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (now, eid, p["type"], op, attr, json.dumps(old), json.dumps(new),
                 p["source"], p["actor"], p["chat_id"], p["message_id"], token),
            )
            revs.append({"attr": attr, "old": old, "new": new, "op": op})

        if p["op"] == "set":
            merged = dict(cur_attrs)
            for k, v in p["attrs"].items():
                if cur_attrs.get(k) != v:
                    merged[k] = v
                    log(k, cur_attrs.get(k), v, "set")
            c.execute(
                """INSERT INTO entity(id,type,name,attrs,created_at,updated_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     type=excluded.type, name=excluded.name,
                     attrs=excluded.attrs, updated_at=excluded.updated_at""",
                (eid, p["type"], p["name"], json.dumps(merged),
                 cur["created_at"] if cur else now, now),
            )
        elif p["op"] == "delete" and cur:
            log(None, cur_attrs, None, "delete")
            c.execute("DELETE FROM entity WHERE id=?", (eid,))

        result = {"ok": True, "entity_id": eid, "revisions": revs}
        c.execute("UPDATE pending SET status='applied', result=? WHERE token=?",
                  (json.dumps(result), token))
    return {**result, "applied": True}


def query(type=None, q=None, limit=50):
    init()
    sql, args, where = "SELECT * FROM entity", [], []
    if type:
        where.append("type=?")
        args.append(type)
    if q:
        where.append("(name LIKE ? OR attrs LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        rows = c.execute(sql, args).fetchall()
    return [
        {"id": r["id"], "type": r["type"], "name": r["name"],
         "attrs": json.loads(r["attrs"] or "{}"), "updated_at": r["updated_at"]}
        for r in rows
    ]


def get(entity_id):
    init()
    with _conn() as c:
        r = c.execute("SELECT * FROM entity WHERE id=?", (entity_id,)).fetchone()
        if not r:
            return None
        revs = c.execute(
            "SELECT ts,op,attr,old_value,new_value,source,actor FROM revision "
            "WHERE entity_id=? ORDER BY id DESC LIMIT 20",
            (entity_id,),
        ).fetchall()
    return {
        "id": r["id"], "type": r["type"], "name": r["name"],
        "attrs": json.loads(r["attrs"] or "{}"),
        "created_at": r["created_at"], "updated_at": r["updated_at"],
        "revisions": [
            {"ts": x["ts"], "op": x["op"], "attr": x["attr"],
             "old": json.loads(x["old_value"] or "null"),
             "new": json.loads(x["new_value"] or "null"),
             "source": x["source"], "actor": x["actor"]}
            for x in revs
        ],
    }


def types():
    init()
    with _conn() as c:
        rows = c.execute("SELECT type, COUNT(*) n FROM entity GROUP BY type").fetchall()
        promoted = {r["type"] for r in c.execute("SELECT type FROM type_schema").fetchall()}
    return [{"type": r["type"], "count": r["n"], "promoted": r["type"] in promoted} for r in rows]


def _validate(attrs, schema):
    """Lightweight JSON-Schema check: required keys present + primitive type match. No external dep."""
    errs = []
    for k in schema.get("required", []):
        if k not in attrs:
            errs.append(f"missing {k}")
    pytypes = {"string": str, "number": (int, float), "boolean": bool, "object": dict, "array": list}
    for k, spec in (schema.get("properties") or {}).items():
        t = spec.get("type")
        if k in attrs and t in pytypes and not isinstance(attrs[k], pytypes[t]):
            errs.append(f"{k} not {t}")
    return errs


def promote(type, json_schema, by="coding-agent"):
    """Codify a type's schema (data-layer self-authoring). All-or-nothing: refuses if any entity is invalid."""
    init()
    now = int(time.time())
    migrated = 0
    invalid = []
    with _conn() as c:
        for r in c.execute("SELECT id, attrs FROM entity WHERE type=?", (type,)).fetchall():
            errs = _validate(json.loads(r["attrs"] or "{}"), json_schema)
            if errs:
                invalid.append({"id": r["id"], "errors": errs})
            else:
                migrated += 1
        if invalid:
            return {"ok": False, "type": type, "migrated": migrated, "invalid": invalid}
        prev = c.execute("SELECT version FROM type_schema WHERE type=?", (type,)).fetchone()
        ver = (prev["version"] if prev else 0) + 1
        c.execute("INSERT OR REPLACE INTO type_schema VALUES(?,?,?,?,?)",
                  (type, json.dumps(json_schema), ver, now, by))
        c.execute(
            """INSERT INTO revision(ts,entity_id,type,op,attr,old_value,new_value,source,actor,token)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (now, f"type:{type}", type, "promote", None, None,
             json.dumps({"version": ver}), "agent", by, None),
        )
    return {"ok": True, "type": type, "migrated": migrated, "invalid": []}


def uncodify(type, by="owner"):
    """Reverse a promote(): drop the type's codified schema so it returns to flexible/un-promoted.
    Entities are untouched (the schema was only a registry entry). Audited + idempotent."""
    init()
    now = int(time.time())
    with _conn() as c:
        row = c.execute("SELECT version FROM type_schema WHERE type=?", (type,)).fetchone()
        if not row:
            return {"ok": True, "type": type, "removed": False}  # already un-codified
        c.execute("DELETE FROM type_schema WHERE type=?", (type,))
        c.execute(
            """INSERT INTO revision(ts,entity_id,type,op,attr,old_value,new_value,source,actor,token)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (now, f"type:{type}", type, "uncodify", None,
             json.dumps({"version": row["version"]}), None, "restore", by, None),
        )
    return {"ok": True, "type": type, "removed": True}


def sweep_pending(ttl=3600):
    """Drop never-committed proposals older than ttl seconds. Returns count removed."""
    init()
    with _conn() as c:
        cur = c.execute("DELETE FROM pending WHERE status='pending' AND created_at < ?",
                        (int(time.time()) - ttl,))
        return cur.rowcount


# --- Grooming analysis -------------------------------------------------------------------------
# Read-only candidate generation for the nightly groom pass. Every actual mutation still flows
# through propose()/commit()/promote() so it lands in the revision log; these helpers only look.

def _name_tokens(name: str) -> set:
    return {t for t in _slug(name).split("-") if t}


def _components(nodes, edges):
    """Connected components via union-find. Returns groups of size >= 2."""
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        parent[find(a)] = find(b)
    groups = defaultdict(list)
    for n in nodes:
        groups[find(n)].append(n)
    return [g for g in groups.values() if len(g) >= 2]


def dedup_clusters(min_name_jaccard=0.5):
    """Group same-type entities that plausibly describe the same real-world thing, for the agent
    to judge. Two entities are linked when their name tokens overlap strongly (Jaccard >= threshold
    or one name's tokens contain the other's) or they share an identical attr key+value. Read-only.
    Returns a list of clusters, each a list of full entity dicts."""
    ents = query(limit=100000)
    by_type = defaultdict(list)
    for e in ents:
        by_type[e["type"]].append(e)

    clusters = []
    for items in by_type.values():
        if len(items) < 2:
            continue
        ids = [e["id"] for e in items]
        toks = {e["id"]: _name_tokens(e["name"] or "") for e in items}
        kv = {e["id"]: {(k, json.dumps(v, sort_keys=True))
                        for k, v in e["attrs"].items() if v is not None} for e in items}
        edges = []
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = ids[i], ids[j]
                ta, tb = toks[a], toks[b]
                union = ta | tb
                jac = len(ta & tb) / len(union) if union else 0
                contained = bool(ta) and bool(tb) and (ta <= tb or tb <= ta)
                shares_kv = bool(kv[a] & kv[b])
                if jac >= min_name_jaccard or contained or shares_kv:
                    edges.append((a, b))
        by_id = {e["id"]: e for e in items}
        for comp in _components(ids, edges):
            clusters.append([by_id[i] for i in comp])
    return clusters


def cluster_conflicts(entities):
    """attr -> list of distinct non-null values present across the cluster. A non-empty result
    means a merge would have to discard data (lossy) and must go to review, not auto-apply."""
    vals = defaultdict(set)
    for e in entities:
        for k, v in (e.get("attrs") or {}).items():
            if v is not None:
                vals[k].add(json.dumps(v, sort_keys=True))
    return {k: [json.loads(x) for x in vs] for k, vs in vals.items() if len(vs) > 1}


def apply_merge(canonical_id, member_ids, by="coder", source="groom", dry_run=False):
    """Fold members into the canonical entity through the gated propose->commit path, so every
    change is audited in the revision log. Lossless by construction: only attrs the canonical is
    missing are filled (it never overwrites its own values); each member is then deleted
    (superseded). Call this only for clusters where cluster_conflicts() is empty."""
    init()
    canon = get(canonical_id)
    if not canon:
        return {"ok": False, "error": "canonical not found"}
    members = [m for m in (get(i) for i in member_ids if i != canonical_id) if m]

    fill = {}
    for m in members:
        for k, v in m["attrs"].items():
            if v is not None and k not in canon["attrs"] and k not in fill:
                fill[k] = v

    plan = {"ok": True, "dry_run": dry_run, "canonical": canonical_id,
            "filled": fill, "superseded": [m["id"] for m in members]}
    if dry_run:
        return plan

    if fill:
        commit(propose("set", entity_id=canonical_id, type=canon["type"], name=canon["name"],
                       attrs=fill, source=source, actor=by)["token"])
    for m in members:
        commit(propose("delete", entity_id=m["id"], type=m["type"], name=m["name"],
                       source=source, actor=by)["token"])
    return plan


def _json_schema_type(v):
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return "string"


def promotion_candidates(min_entities=3, min_coverage=0.8):
    """Un-promoted types whose shape has stabilized: a consistent attribute set shared across
    enough entities. Derives a JSON schema (attrs present in >= min_coverage of entities become
    required; observed value types become properties). Read-only; the agent decides whether to
    promote() each one."""
    init()
    promoted = {t["type"] for t in types() if t["promoted"]}
    by_type = defaultdict(list)
    for e in query(limit=100000):
        by_type[e["type"]].append(e["attrs"])

    cands = []
    for type_, attrs_list in by_type.items():
        if type_ in promoted or len(attrs_list) < min_entities:
            continue
        n = len(attrs_list)
        key_counts = Counter()
        type_votes = defaultdict(Counter)
        for a in attrs_list:
            for k, v in a.items():
                key_counts[k] += 1
                type_votes[k][_json_schema_type(v)] += 1
        required = sorted(k for k, c in key_counts.items() if c / n >= min_coverage)
        if not required:
            continue
        props = {k: {"type": type_votes[k].most_common(1)[0][0]} for k in key_counts}
        cands.append({
            "type": type_, "entities": n,
            "coverage": round(min(key_counts[k] / n for k in required), 3),
            "schema": {"type": "object", "required": required, "properties": props},
        })
    return cands


def orphan_entities():
    """Entities left with no attributes (e.g. everything was merged away). Read-only."""
    init()
    with _conn() as c:
        rows = c.execute("SELECT id, type, name, attrs FROM entity").fetchall()
    return [{"id": r["id"], "type": r["type"], "name": r["name"]}
            for r in rows if not json.loads(r["attrs"] or "{}")]


def empty_types():
    """Types codified in the registry that no longer have any entities. Reported, not auto-dropped."""
    init()
    with _conn() as c:
        have = {r["type"] for r in c.execute("SELECT DISTINCT type FROM entity").fetchall()}
        reg = [r["type"] for r in c.execute("SELECT type FROM type_schema").fetchall()]
    return [t for t in reg if t not in have]


def stale_entities(age_days=180):
    """Entities not updated in age_days, surfaced for a human to confirm or refresh."""
    init()
    cutoff = int(time.time()) - age_days * 86400
    with _conn() as c:
        rows = c.execute(
            "SELECT id, type, name, updated_at FROM entity WHERE updated_at < ? ORDER BY updated_at",
            (cutoff,),
        ).fetchall()
    return [{"id": r["id"], "type": r["type"], "name": r["name"], "updated_at": r["updated_at"]}
            for r in rows]


def stale_by_last_verified(age_days=180, types=None):
    """Durable facts overdue for re-verification. Keys off the `last_verified` attrs
    convention (a unix ts), falling back to `updated_at` when a fact was never explicitly verified —
    an unrelated edit must NOT count as a re-verification, so this is distinct from stale_entities().
    `types`: optional iterable to restrict to durable-fact types (e.g. exclude live_source pointers).
    Returns each overdue fact with the age signal that flagged it."""
    init()
    now = int(time.time())
    cutoff = now - age_days * 86400
    type_set = set(types) if types else None
    out = []
    with _conn() as c:
        rows = c.execute("SELECT id, type, name, attrs, updated_at FROM entity").fetchall()
    for r in rows:
        if type_set is not None and r["type"] not in type_set:
            continue
        attrs = json.loads(r["attrs"] or "{}")
        lv = attrs.get("last_verified")
        basis, ts = ("last_verified", lv) if isinstance(lv, (int, float)) else ("updated_at", r["updated_at"])
        if ts is not None and ts < cutoff:
            out.append({"id": r["id"], "type": r["type"], "name": r["name"],
                        "basis": basis, "verified_at": int(ts), "age_days": (now - int(ts)) // 86400})
    return sorted(out, key=lambda x: x["verified_at"])
