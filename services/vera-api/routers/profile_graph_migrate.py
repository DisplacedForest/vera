"""Profile Graph migration — seeds the graph from the existing structured state so Pulse
starts from what Vera already knows.

Deterministic and idempotent: it keys on (type, label), so re-running is safe and a no-op.
Interests become interest nodes (their gloss is a durable fact, their weight the seed
engagement); standing journal commitments become watch nodes; hand-mapped identity facts
become typed fact-bearing nodes. Inputs are structured: anything needing language judgement
arrives as `seed_facts`, already parsed by the extraction job.
"""
from . import profile_graph_store as pg


def _facts(raw, source):
    """Normalize migration fact inputs (plain strings or already-structured fact dicts) into
    provenance-bearing facts. Idempotent re-runs dedup by text, so a stable timestamp is not
    required for the seed to be a no-op."""
    out = []
    for f in raw or []:
        if isinstance(f, dict) and f.get("text"):
            out.append(f if f.get("source") else {**f, "source": source})
        elif isinstance(f, str) and f.strip():
            out.append(pg.make_fact(f.strip(), source=source))
    return out


def migrate(*, interests=None, journal_entries=None, seed_facts=None):
    """Seed the graph from structured snapshots. Returns per-type counts. Idempotent."""
    counts = {"interest": 0, "watch": 0, "seed": 0}
    for it in interests or []:
        topic = (it.get("topic") or "").strip()
        if not topic:
            continue
        gloss = (it.get("gloss") or "").strip()
        pg.upsert_by_label(type="interest", label=topic,
                           facts=_facts([gloss] if gloss else [], "migration:interest"),
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
        pg.upsert_by_label(type=s["type"], label=label,
                           facts=_facts(s.get("facts"), "migration:seed"))
        counts["seed"] += 1
    return counts
