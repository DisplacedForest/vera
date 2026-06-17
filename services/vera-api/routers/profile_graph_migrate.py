"""Profile Graph migration — the one-time seed that brings the existing structured state
into the graph so Pulse v2 starts from what Vera already knows.

Deterministic and idempotent: it keys on (type, label) rather than embeddings, so it runs
safely at every boot until the conversation-extraction job becomes the live write path.
Interests become interest nodes (their gloss is a durable fact, their weight the seed
engagement); standing journal commitments become watch nodes; hand-mapped identity facts
become typed fact-bearing nodes. Free-text memory understanding is the extraction job's
domain, not this seed's: anything needing language judgement arrives as `seed_facts`,
already structured.
"""
from . import profile_graph_store as pg


def migrate(*, interests=None, journal_entries=None, seed_facts=None):
    """Seed the graph from structured snapshots. Returns per-type counts. Idempotent."""
    counts = {"interest": 0, "watch": 0, "seed": 0}
    for it in interests or []:
        topic = (it.get("topic") or "").strip()
        if not topic:
            continue
        gloss = (it.get("gloss") or "").strip()
        pg.upsert_by_label(type="interest", label=topic,
                           facts=[gloss] if gloss else [],
                           engagement=float(it.get("weight") or 0.0))
        counts["interest"] += 1
    for j in journal_entries or []:
        heading = (j.get("heading") or "").strip()
        if not heading:
            continue
        pg.upsert_by_label(type="watch", label=heading, state="active",
                           resolve_condition=j.get("resolve_condition"),
                           next_check=j.get("next_check"))
        counts["watch"] += 1
    for s in seed_facts or []:
        label = (s.get("label") or "").strip()
        if not label or not s.get("type"):
            continue
        pg.upsert_by_label(type=s["type"], label=label, facts=s.get("facts") or [])
        counts["seed"] += 1
    return counts
