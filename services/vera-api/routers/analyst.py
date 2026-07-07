"""Analyst Agent — the Pulse pipeline's ranking stage.

Scores every Scout candidate cheaply and keeps the top ~8. The score is one transparent
weighted sum of five terms, each in [0,1] and each a deterministic number from embeddings,
dates, and graph weights:

    score = 0.35·relevance + 0.25·novelty + 0.20·opportunity + 0.15·urgency + 0.05·serendipity

The only model call in the path is a cheap structured opportunity classifier, used as a
numeric feature, never a gate. Every weight and threshold is a declared, env-tunable constant
(the audit surface). All I/O — embeddings, the classifier — is injected, so a run is offline
and reproducible.
"""
import os
from datetime import datetime, timezone

from . import profile_graph_store as pg


def _envf(name, default):
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return float(default)


W_RELEVANCE = _envf("PULSE_W_RELEVANCE", "0.35")
W_NOVELTY = _envf("PULSE_W_NOVELTY", "0.25")
W_OPPORTUNITY = _envf("PULSE_W_OPPORTUNITY", "0.20")
W_URGENCY = _envf("PULSE_W_URGENCY", "0.15")
W_SERENDIPITY = _envf("PULSE_W_SERENDIPITY", "0.05")

NOVELTY_FLOOR = _envf("PULSE_NOVELTY_FLOOR", "0.10")   # finding below this is dropped pre-classifier
URGENCY_DECAY = _envf("PULSE_URGENCY_DECAY", "0.85")   # per-day decay of a past event's urgency
URGENCY_WINDOW = _envf("PULSE_URGENCY_WINDOW", "7.0")  # days; a deadline this near scores ~1.0
UNDATED_URGENCY = _envf("PULSE_UNDATED_URGENCY", "0.10")
EPSILON = _envf("PULSE_EPSILON", "0.1")               # exploration share: the reserved serendipity slot

LOG_PATH = os.environ.get("ANALYST_LOG_PATH", "/data/analyst_log.jsonl")


def _weights():
    """The five ranking weights: the learned coefficients once the feedback fit has installed
    them, else the hand-set env constants. Read at call time so a fresh fit takes effect with no
    restart."""
    try:
        from . import learn_store
        w = learn_store.get_weights()
        if w and w.get("coeffs"):
            c = w["coeffs"]
            return (c.get("relevance", W_RELEVANCE), c.get("novelty", W_NOVELTY),
                    c.get("opportunity", W_OPPORTUNITY), c.get("urgency", W_URGENCY),
                    c.get("serendipity", W_SERENDIPITY))
    except Exception:
        pass
    return (W_RELEVANCE, W_NOVELTY, W_OPPORTUNITY, W_URGENCY, W_SERENDIPITY)


def _clamp01(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def _to_day(date_str):
    """A YYYY-MM-DD string to a UTC date, or None when unparseable."""
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc).date()
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- urgency


def urgency(event_date, deadline, now):
    """Date arithmetic in [0,1]. A future deadline scales as `WINDOW / days_left` (nearer is
    higher); a past event decays as `URGENCY_DECAY^days`; anything undated sits at the baseline."""
    today = datetime.fromtimestamp(now, timezone.utc).date()
    dl = _to_day(deadline)
    if dl is not None:
        days_left = (dl - today).days
        if days_left >= 0:
            return _clamp01(URGENCY_WINDOW / max(days_left, 0.5))
    ev = _to_day(event_date)
    if ev is not None:
        days = (today - ev).days
        if days >= 0:
            return _clamp01(URGENCY_DECAY ** days)
    return UNDATED_URGENCY


# --------------------------------------------------------------------------- novelty


def novelty(finding_emb, corpus_embs):
    """`1 − max cosine(finding, corpus)` in [0,1]: 1.0 when the finding has no embedding or the
    corpus is empty (nothing to be similar to), shrinking toward 0 as the finding nears anything
    recently briefed."""
    if not finding_emb or not corpus_embs:
        return 1.0
    nearest = max(pg._cosine(finding_emb, c) for c in corpus_embs if c)
    return _clamp01(1.0 - nearest)


# --------------------------------------------------------------------------- relevance


def _normalize(values):
    """Scale a list of non-negative values to [0,1] by their max; all-zero stays all-zero."""
    top = max(values, default=0.0)
    if top <= 0:
        return [0.0 for _ in values]
    return [v / top for v in values]


def relevance_scores(seed_ids, now, similarities=None):
    """The per-candidate relevance term: spreading-activation relevance seeded at each
    candidate's node, scaled by the candidate's content similarity when provided, then
    max-normalized across the run so the strongest candidate is 1.0."""
    raw = [pg.relevance([sid], now) if sid else 0.0 for sid in seed_ids]
    if similarities is not None:
        raw = [r * s for r, s in zip(raw, similarities)]
    return _normalize(raw)


def content_similarity(finding_emb, node_emb):
    """cosine(finding, seed node) clamped to [0,1]; 1.0 when either embedding is missing
    (relevance falls back to the seed node's graph relevance alone)."""
    if not finding_emb or not node_emb:
        return 1.0
    return _clamp01(pg._cosine(finding_emb, node_emb))


async def _node_embedding(nid, cache):
    """The seed node's embedding for the similarity term: the persisted vector when the
    node carries one, else computed and persisted via embed_node when embeddings are
    wired; None otherwise. Cached per run."""
    if nid in cache:
        return cache[nid]
    node = pg.get_node(nid) if nid else None
    vec = (node or {}).get("embedding")
    if vec is None and node is not None and pg.embeddings_configured():
        vec = await pg.embed_node(nid)
    cache[nid] = vec
    return vec


# --------------------------------------------------------------------------- opportunity

_ACTIVE_PROJECT_TYPES = {"project", "goal"}
_INACTIVE = {"dormant", "resolved"}


def _is_active_project(node):
    return (node is not None and node.get("type") in _ACTIVE_PROJECT_TYPES
            and node.get("state") not in _INACTIVE)


def project_connection(seed_id):
    """Graph math: 1.0 when `seed_id` is, or links within MAX_HOPS to, an active project or
    goal node; else 0.0. The multiplier that makes opportunity fire only for actionable findings
    wired to live work."""
    if not seed_id:
        return 0.0
    seen = {seed_id}
    frontier = [(seed_id, 0)]
    while frontier:
        nid, hops = frontier.pop(0)
        if _is_active_project(pg.get_node(nid)):
            return 1.0
        if hops >= pg.MAX_HOPS:
            continue
        for e in pg.neighbors_undirected(nid):
            other = e["other_id"]
            if other not in seen:
                seen.add(other)
                frontier.append((other, hops + 1))
    return 0.0


def opportunity(actionable, project_connection_value):
    """`actionable × project_connection`, clamped to [0,1]. Pure information (no live project)
    scores 0 however actionable it reads."""
    return _clamp01(float(actionable or 0.0) * float(project_connection_value or 0.0))


# --------------------------------------------------------------------------- serendipity


def serendipity(seed_id, top_k_ids):
    """`DAMPING^hops` for a candidate whose seed node sits within MAX_HOPS of a top-k node,
    rewarding the adjacent-but-quieter neighbours of what the run is already exploiting. A top-k
    member or a node with no path to one scores 0."""
    top = set(top_k_ids or [])
    if not seed_id or seed_id in top:
        return 0.0
    seen = {seed_id}
    frontier = [(seed_id, 0)]
    while frontier:
        nid, hops = frontier.pop(0)
        if hops > 0 and nid in top:
            return _clamp01(pg.DAMPING ** hops)
        if hops >= pg.MAX_HOPS:
            continue
        for e in pg.neighbors_undirected(nid):
            other = e["other_id"]
            if other not in seen:
                seen.add(other)
                frontier.append((other, hops + 1))
    return 0.0


# --------------------------------------------------------------------------- opportunity classifier

CLASSIFY_SYS = (
    "You read one candidate finding for someone's personal briefing and judge only whether it "
    "is something they could ACT on. Reply ONLY with JSON: "
    '{"actionable": <0-1 confidence it affords a concrete action>, '
    '"deadline": "<YYYY-MM-DD or null>", "action": "<short action phrase or null>"}. '
    "Pure information with nothing to do scores near 0."
)


async def classify_opportunity(finding_text, llm=None):
    """The one model call in the path: a cheap structured classifier tagging a finding's
    actionability and any deadline. Used as a numeric feature, never a gate. Degrades to a
    not-actionable verdict on any failure."""
    import json
    if llm is None:
        from .pulse import _vera as llm
    try:
        raw = await llm([{"role": "system", "content": CLASSIFY_SYS},
                         {"role": "user", "content": finding_text}], temperature=0.0)
        j = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    except Exception:
        return {"actionable": 0.0, "deadline": None, "action": None}
    return {"actionable": _clamp01(float(j.get("actionable") or 0.0)),
            "deadline": j.get("deadline") or None, "action": j.get("action") or None}


# --------------------------------------------------------------------------- ranking

MAX_CARDS = int(_envf("PULSE_MAX_CARDS", "8"))
MAX_PER_INTEREST = int(_envf("PULSE_MAX_PER_INTEREST", "1"))


def _terms(s):
    return {"relevance": s["relevance"], "novelty": s["novelty"], "opportunity": s["opportunity"],
            "urgency": s["urgency"], "serendipity": s["serendipity"], "total": s["total"]}


def _log_run(scored, floored, chosen_ids):
    """Append one structured line per run capturing every term for every candidate plus whether
    it was chosen — the labeled dataset the learned-weight fit reads. Best-effort, never raises."""
    import json
    import time
    try:
        rows = []
        for s in scored:
            rows.append({"url": s["cand"]["url"], "seed": s["cand"]["seed_node_id"],
                         **_terms(s), "chosen": id(s["cand"]) in chosen_ids, "floored": False})
        for cand, nov in floored:
            rows.append({"url": cand["url"], "seed": cand["seed_node_id"], "relevance": 0.0,
                         "novelty": nov, "opportunity": 0.0, "urgency": 0.0, "serendipity": 0.0,
                         "total": 0.0, "chosen": False, "floored": True})
        wr, wn, wo, wu, ws = _weights()
        rec = {"ts": int(time.time()),
               "weights": {"relevance": wr, "novelty": wn, "opportunity": wo,
                           "urgency": wu, "serendipity": ws},
               "candidates": rows}
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def _apply_spread_cap(ranked, max_cards, max_per):
    chosen, per = [], {}
    for s in ranked:
        seed = s["cand"]["seed_node_id"]
        if per.get(seed, 0) >= max_per:
            continue
        chosen.append(s)
        per[seed] = per.get(seed, 0) + 1
        if len(chosen) >= max_cards:
            break
    return chosen


def _reserve_serendipity(chosen, ranked, max_cards):
    """Honor the epsilon-greedy reserved slot: ensure the highest-serendipity candidate the
    exploitation ranking crowded out still surfaces, marked `reserved`."""
    chosen_ids = {id(s) for s in chosen}
    pool = [s for s in ranked if id(s) not in chosen_ids and s["serendipity"] > 0]
    if not pool:
        return chosen
    best = max(pool, key=lambda s: (s["serendipity"], s["total"], s["cand"]["url"]))
    if len(chosen) >= max_cards:
        droppable = [s for s in chosen if not s["cand"].get("reserved")]
        if not droppable:
            return chosen
        chosen.remove(min(droppable, key=lambda s: (s["total"], s["cand"]["url"])))
    best["cand"]["reserved"] = True
    chosen.append(best)
    return chosen


async def rank(candidates, *, now=None, recent_card_texts=None, classify=None, embed=None,
               max_cards=None, max_per_interest=None):
    """Score every Scout candidate and return the top survivors. Embeds each finding, drops
    anything below the novelty floor before the classifier (the one cheap pre-filter), runs the
    opportunity classifier on survivors, then computes the five weighted terms entirely from
    embeddings, dates, and graph weights. Applies the per-node spread cap and the reserved
    serendipity slot, logs every term for every candidate, and returns `{chosen, considered}` —
    each candidate carrying its term breakdown."""
    import time
    now = int(time.time()) if now is None else now
    max_cards = MAX_CARDS if max_cards is None else max_cards
    max_per = MAX_PER_INTEREST if max_per_interest is None else max_per_interest
    embed = pg.embed if embed is None else embed
    classify = classify_opportunity if classify is None else classify

    corpus = [c for c in [await embed(t) for t in (recent_card_texts or [])] if c]

    scored, floored = [], []
    for c in candidates:
        emb_v = await embed(c["finding_text"])
        nov = novelty(emb_v, corpus)
        if nov < NOVELTY_FLOOR:
            floored.append((c, nov))
            continue
        scored.append({"cand": c, "novelty": nov, "emb": emb_v})

    wr, wn, wo, wu, ws = _weights()
    node_embs = {}
    sims = [content_similarity(s["emb"], await _node_embedding(s["cand"]["seed_node_id"], node_embs))
            for s in scored]
    rel = relevance_scores([s["cand"]["seed_node_id"] for s in scored], now, similarities=sims)
    for i, s in enumerate(scored):
        s["relevance"] = rel[i]
        cls = await classify(s["cand"]["finding_text"]) or {}
        s["cls"] = cls
        s["opportunity"] = opportunity(cls.get("actionable"),
                                       project_connection(s["cand"]["seed_node_id"]))
        s["urgency"] = urgency(s["cand"].get("published_date"), cls.get("deadline"), now)
        s["exploit"] = (wr * s["relevance"] + wn * s["novelty"]
                        + wo * s["opportunity"] + wu * s["urgency"])

    by_exploit = sorted(scored, key=lambda s: (-s["exploit"], s["cand"]["url"]))
    top_k_ids = {s["cand"]["seed_node_id"] for s in by_exploit[:max_cards]}
    for s in scored:
        s["serendipity"] = serendipity(s["cand"]["seed_node_id"], top_k_ids)
        s["total"] = s["exploit"] + ws * s["serendipity"]
        s["cand"]["scores"] = _terms(s)
        s["cand"]["classification"] = s["cls"]
        s["cand"].setdefault("reserved", False)

    ranked = sorted(scored, key=lambda s: (-s["total"], s["cand"]["url"]))
    chosen = _apply_spread_cap(ranked, max_cards, max_per)
    chosen = _reserve_serendipity(chosen, ranked, max_cards)

    _log_run(scored, floored, {id(s["cand"]) for s in chosen})
    return {"chosen": [s["cand"] for s in chosen], "considered": [s["cand"] for s in scored]}
