"""Profile Graph store — the single source of truth for what Vera knows about the owner.

A typed node/edge graph. Each node carries a durable FACT layer (never decays) and a
decaying ENGAGEMENT weight (the only thing that drives proactive research): a topic stays
true while its pull on the feed tracks how recently it has been discussed.

Three deterministic math helpers:
  - engagement decay on read (an exponential half-life),
  - cosine dedup-merge (paraphrases of one topic collapse onto one node),
  - spreading activation (a finding inherits the engagement of the nodes it connects to).

Follows the `*_store.py` shape: an env-bound SQLite path, `_conn()`, and an `init()` that
creates tables idempotently. The embeddings call and the dedup tie-break are injected so the
math path runs offline.
"""
import json
import math
import os
import sqlite3
import time
import uuid

DB_PATH = os.environ.get("PROFILE_GRAPH_DB_PATH", "/data/profile_graph/store.db")

# Declared constants — the audit surface, env-overridable, never LLM-chosen.
def _envf(name, default):
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return float(default)


DECAY = _envf("PROFILE_GRAPH_DECAY", "0.97")            # per-day engagement decay (~23-day half-life)
INTERACTION_BONUS = _envf("PROFILE_GRAPH_BONUS", "1.0")  # engagement added per fresh mention
DEDUP_SAME = _envf("PROFILE_GRAPH_DEDUP_SAME", "0.90")   # cosine >= this -> same node (collapse)
DEDUP_NEW = _envf("PROFILE_GRAPH_DEDUP_NEW", "0.78")     # cosine <  this -> new node; between -> tie-break
DAMPING = _envf("PROFILE_GRAPH_DAMPING", "0.5")          # per-hop attenuation in spreading activation
MAX_HOPS = int(_envf("PROFILE_GRAPH_MAX_HOPS", "2"))     # neighbourhood radius for relevance


def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS node (
                id TEXT PRIMARY KEY,
                type TEXT,                 -- project|interest|goal|person|company|location|asset|thread|watch
                label TEXT,                -- canonical display label
                aliases TEXT,              -- json [str] — every paraphrase that collapsed here
                facts TEXT,                -- json [str] — durable, never decays
                engagement REAL,           -- decaying weight; the only driver of research
                last_engaged INTEGER,      -- for lazy decay
                state TEXT,                -- active|dormant|resolved (projects/threads/watches)
                resolve_condition TEXT,    -- watches only
                next_check INTEGER,        -- watches only
                confidence REAL,
                embedding TEXT,            -- json [float] — canonical-label vector
                created_at INTEGER,
                updated_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS edge (
                src_id TEXT,
                dst_id TEXT,
                type TEXT,                 -- supports|depends_on|related_to|part_of|about|at_location
                weight REAL,
                PRIMARY KEY (src_id, dst_id, type)
            );
            CREATE INDEX IF NOT EXISTS idx_node_type ON node(type);
            CREATE INDEX IF NOT EXISTS idx_edge_src ON edge(src_id);
            """
        )


def make_fact(text, source, observed_at=None):
    """A durable fact with provenance: its text, where it came from (e.g. 'owui-memory',
    'chat:<id>', 'extraction:<conv>', 'migration:<store>'), and when it was observed."""
    return {"text": text, "source": source,
            "observed_at": int(time.time()) if observed_at is None else observed_at}


def _merge_facts(existing, new):
    """Union two fact lists, deduped by `text` — the first occurrence's provenance survives,
    so a fact re-observed from a different source keeps its original attribution."""
    out, seen = [], set()
    for f in list(existing or []) + list(new or []):
        t = f.get("text") if isinstance(f, dict) else f
        if t in seen:
            continue
        seen.add(t)
        out.append(f)
    return out


def _row(r):
    return {
        "id": r["id"], "type": r["type"], "label": r["label"],
        "aliases": json.loads(r["aliases"]) if r["aliases"] else [],
        "facts": json.loads(r["facts"]) if r["facts"] else [],
        "engagement": r["engagement"] or 0.0, "last_engaged": r["last_engaged"],
        "state": r["state"], "resolve_condition": r["resolve_condition"],
        "next_check": r["next_check"], "confidence": r["confidence"],
        "embedding": json.loads(r["embedding"]) if r["embedding"] else None,
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


def upsert_node(*, type, label, id=None, aliases=None, facts=None, engagement=0.0,
                last_engaged=None, state=None, resolve_condition=None, next_check=None,
                confidence=None, embedding=None):
    """Insert a node (or overwrite by id). Returns the node id. Callers that want
    paraphrase-collapsing should go through `merge_or_create`, not this."""
    init()
    now = int(time.time())
    nid = id or uuid.uuid4().hex[:16]
    with _conn() as c:
        existing = c.execute("SELECT created_at FROM node WHERE id=?", (nid,)).fetchone()
        created = existing["created_at"] if existing else now
        c.execute(
            """INSERT INTO node(id,type,label,aliases,facts,engagement,last_engaged,state,
                                resolve_condition,next_check,confidence,embedding,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 type=excluded.type, label=excluded.label, aliases=excluded.aliases,
                 facts=excluded.facts, engagement=excluded.engagement,
                 last_engaged=excluded.last_engaged, state=excluded.state,
                 resolve_condition=excluded.resolve_condition, next_check=excluded.next_check,
                 confidence=excluded.confidence, embedding=excluded.embedding,
                 updated_at=excluded.updated_at""",
            (nid, type, label, json.dumps(aliases or []), json.dumps(facts or []),
             engagement, last_engaged if last_engaged is not None else now, state,
             resolve_condition, next_check, confidence,
             json.dumps(embedding) if embedding is not None else None, created, now),
        )
    return nid


def get_node(nid):
    init()
    with _conn() as c:
        r = c.execute("SELECT * FROM node WHERE id=?", (nid,)).fetchone()
    return _row(r) if r else None


def node_by_label(type, label):
    """The node with this exact (type, label), or None. Exact-match identity used by the
    migration seed so it stays idempotent without depending on an embeddings endpoint."""
    init()
    with _conn() as c:
        r = c.execute("SELECT * FROM node WHERE type=? AND label=?", (type, label)).fetchone()
    return _row(r) if r else None


def upsert_by_label(*, type, label, facts=None, engagement=None, state=None,
                    resolve_condition=None, next_check=None):
    """Idempotent upsert keyed on (type, label): create the node, or update an existing one
    by unioning its facts and setting (not accumulating) the given fields. Re-running with the
    same input is a no-op. Returns the node id."""
    existing = node_by_label(type, label)
    if existing:
        merged_facts = _merge_facts(existing["facts"], facts)
        return upsert_node(
            id=existing["id"], type=type, label=label, aliases=existing["aliases"],
            facts=merged_facts,
            engagement=existing["engagement"] if engagement is None else engagement,
            last_engaged=existing["last_engaged"],
            state=state if state is not None else existing["state"],
            resolve_condition=resolve_condition if resolve_condition is not None else existing["resolve_condition"],
            next_check=next_check if next_check is not None else existing["next_check"],
            confidence=existing["confidence"], embedding=existing["embedding"])
    return upsert_node(type=type, label=label, aliases=[label], facts=facts or [],
                       engagement=engagement or 0.0, state=state,
                       resolve_condition=resolve_condition, next_check=next_check)


def all_nodes(type=None):
    init()
    with _conn() as c:
        if type:
            rows = c.execute("SELECT * FROM node WHERE type=?", (type,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM node").fetchall()
    return [_row(r) for r in rows]


def add_edge(src_id, dst_id, type, weight=1.0):
    init()
    with _conn() as c:
        c.execute(
            """INSERT INTO edge(src_id,dst_id,type,weight) VALUES(?,?,?,?)
               ON CONFLICT(src_id,dst_id,type) DO UPDATE SET weight=excluded.weight""",
            (src_id, dst_id, type, weight),
        )


def neighbors(nid):
    init()
    with _conn() as c:
        rows = c.execute("SELECT src_id,dst_id,type,weight FROM edge WHERE src_id=?",
                         (nid,)).fetchall()
    return [{"src_id": r["src_id"], "dst_id": r["dst_id"], "type": r["type"],
             "weight": r["weight"]} for r in rows]


def neighbors_undirected(nid):
    """Edges incident to `nid` in either direction, as `{other_id, type, weight}`. Connectivity
    questions (e.g. is a node linked to a project) ignore edge direction."""
    init()
    with _conn() as c:
        rows = c.execute("SELECT src_id,dst_id,type,weight FROM edge WHERE src_id=? OR dst_id=?",
                         (nid, nid)).fetchall()
    return [{"other_id": r["dst_id"] if r["src_id"] == nid else r["src_id"],
             "type": r["type"], "weight": r["weight"]} for r in rows]


# --------------------------------------------------------------------------- math: decay


def engagement_now(node, now=None):
    """The node's engagement decayed to `now`: stored × DECAY^(days since last engaged).
    Facts are untouched — only the pull on the feed fades, never the underlying truth."""
    now = int(time.time()) if now is None else now
    last = node.get("last_engaged") or node.get("created_at") or now
    days = max(0.0, (now - last) / 86400.0)
    return (node.get("engagement") or 0.0) * (DECAY ** days)


def bump_engagement(nid, now=None, recency_factor=1.0):
    """Register a fresh engagement: decay the stored weight to `now`, add the interaction
    bonus (scaled by how recent the source is), and restamp `last_engaged`."""
    now = int(time.time()) if now is None else now
    node = get_node(nid)
    if not node:
        return None
    new_weight = engagement_now(node, now) + INTERACTION_BONUS * recency_factor
    with _conn() as c:
        c.execute("UPDATE node SET engagement=?, last_engaged=?, updated_at=? WHERE id=?",
                  (new_weight, now, now, nid))
    return new_weight


# --------------------------------------------------------------------------- math: dedup-merge


def _cosine(a, b):
    """Cosine similarity of two equal-length vectors. 0.0 when either is the zero vector."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _best_match(type, embedding):
    """The same-type node whose embedding is closest to `embedding`, and that cosine."""
    best, best_cos = None, -1.0
    for n in all_nodes(type=type):
        emb = n.get("embedding")
        if not emb:
            continue
        cos = _cosine(embedding, emb)
        if cos > best_cos:
            best, best_cos = n, cos
    return best, best_cos


def _label_or_tiebreak_match(type, label, tiebreak):
    """The degradation path used when no embedding is available: match an existing same-type
    node by exact label, else let the injected `tiebreak` canonicalize a paraphrase, else None."""
    exact = node_by_label(type, label)
    if exact:
        return exact
    if tiebreak is not None:
        for n in all_nodes(type=type):
            if tiebreak(label, n):
                return n
    return None


def merge_or_create(*, type, label, embedding, facts=None, now=None, tiebreak=None,
                    recency_factor=1.0, state=None, resolve_condition=None, next_check=None):
    """Add an observation of `label` to the graph, collapsing paraphrases onto one node.

    With an embedding: cosine against existing same-type nodes >= DEDUP_SAME merges (records the
    label as an alias, bumps engagement, unions the facts); < DEDUP_NEW creates a new node; the
    gray band consults `tiebreak(label, node) -> bool` (an injected LLM judgement, the only model
    call here). With no embedding (the unconfigured-endpoint degradation path): match on exact
    label, then on the `tiebreak` canonicalization, else create. Returns the node id."""
    now = int(time.time()) if now is None else now
    facts = facts or []
    if embedding is None:
        match = _label_or_tiebreak_match(type, label, tiebreak)
    else:
        cand, cos = _best_match(type, embedding)
        match = cand if (cand and (
            cos >= DEDUP_SAME
            or (cos >= DEDUP_NEW and tiebreak is not None and tiebreak(label, cand)))) else None
    if match:
        aliases = list(dict.fromkeys(match["aliases"] + [label]))
        merged_facts = _merge_facts(match["facts"], facts)
        upsert_node(id=match["id"], type=type, label=match["label"], aliases=aliases,
                    facts=merged_facts, engagement=match["engagement"],
                    last_engaged=match["last_engaged"], state=match["state"] or state,
                    resolve_condition=match["resolve_condition"] or resolve_condition,
                    next_check=match["next_check"] or next_check,
                    confidence=match["confidence"], embedding=match["embedding"])
        bump_engagement(match["id"], now=now, recency_factor=recency_factor)
        return match["id"]
    nid = upsert_node(type=type, label=label, aliases=[label], facts=facts,
                      engagement=0.0, last_engaged=now, state=state,
                      resolve_condition=resolve_condition, next_check=next_check,
                      embedding=embedding)
    bump_engagement(nid, now=now, recency_factor=recency_factor)
    return nid


# --------------------------------------------------------------------------- math: relevance


def relevance(seed_ids, now=None):
    """Spreading-activation relevance of a finding seeded at `seed_ids`: each node within
    MAX_HOPS contributes its decayed engagement, attenuated by the product of the edge
    weights along the path and DAMPING^hops. Personalized-PageRank-lite, fully deterministic.
    A node is counted once, at the shortest hop it is first reached."""
    now = int(time.time()) if now is None else now
    reached = {}                                   # node id -> (hops, path_weight)
    frontier = [(sid, 0, 1.0) for sid in seed_ids]
    for sid in seed_ids:
        reached[sid] = (0, 1.0)
    while frontier:
        nid, hops, pweight = frontier.pop(0)
        if hops >= MAX_HOPS:
            continue
        for e in neighbors(nid):
            dst, w = e["dst_id"], e["weight"] if e["weight"] is not None else 1.0
            if dst in reached:
                continue
            reached[dst] = (hops + 1, pweight * w)
            frontier.append((dst, hops + 1, pweight * w))
    total = 0.0
    for nid, (hops, pweight) in reached.items():
        node = get_node(nid)
        if not node:
            continue
        total += engagement_now(node, now) * pweight * (DAMPING ** hops)
    return total


# --------------------------------------------------------------------------- embeddings


def embeddings_configured():
    """True when an embeddings endpoint is wired. The dedup/novelty math is sharper with
    one; unconfigured, callers fall back to an LLM canonicalization tie-break."""
    return bool(os.environ.get("VERA_EMBED_URL", "").strip())


async def _embeddings_post(url, payload):
    """The raw POST to an OpenAI-compatible /v1/embeddings endpoint. Isolated so tests can
    substitute a canned response without a live endpoint."""
    import aiohttp
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as r:
            r.raise_for_status()
            return await r.json()


async def embed(text):
    """Embed `text` via the configured OpenAI-compatible /v1/embeddings endpoint, or return
    None when no endpoint is set (graceful degradation, no LAN default). Read at call time so
    a deployment can wire the endpoint without a restart."""
    base = os.environ.get("VERA_EMBED_URL", "").strip().rstrip("/")
    if not base:
        return None
    model = os.environ.get("VERA_EMBED_MODEL", "").strip()
    try:
        d = await _embeddings_post(f"{base}/embeddings", {"model": model, "input": text})
        return (d.get("data") or [{}])[0].get("embedding")
    except Exception:
        return None


async def embed_node(nid):
    """Embed a node's canonical label and persist the vector back onto the node. Returns the
    vector, or None when no endpoint is configured (the node is left unembedded). This is how
    a node acquires the embedding the dedup/novelty math reads."""
    node = get_node(nid)
    if not node:
        return None
    vec = await embed(node["label"])
    if vec is None:
        return None
    with _conn() as c:
        c.execute("UPDATE node SET embedding=?, updated_at=? WHERE id=?",
                  (json.dumps(vec), int(time.time()), nid))
    return vec
