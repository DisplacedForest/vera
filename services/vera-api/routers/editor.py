"""Editor Agent — deep-research synthesis support for the Pulse pipeline's final stage.

Holds the Editor's deterministic, testable logic: a date-aware staleness check that the claim
auditor consults, cross-domain link gathering from the Profile Graph, the Analyst-survivor to
research-topic mapping, and the journal-as-view rendering plus its resolve/fold graph ops. The
heavy LLM work (deep research, first-person synthesis) stays in pulse.py's `research_topic`;
this module supplies the math and graph reads around it.
"""
import os
import re
from datetime import datetime, timezone

from . import profile_graph_store as pg


def _envf(name, default):
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return float(default)


STALE_CLAIM_DAYS = _envf("PULSE_STALE_CLAIM_DAYS", "120")   # a season; older sourcing fails a current claim
LINK_ENGAGEMENT_FLOOR = _envf("PULSE_LINK_ENGAGEMENT_FLOOR", "0.5")  # an active neighbour worth surfacing
LINK_LIMIT = int(_envf("PULSE_LINK_LIMIT", "4"))


# --------------------------------------------------------------------------- date-aware staleness

_PRESENT_MARKERS = ("currently", "is now", "are now", "as of", "remains", "still the",
                    "is the current", "presently", "to this day", "at present")
_CITE = re.compile(r"\[(\d+)\]")
_SENTENCE = re.compile(r"[^.!?]*[.!?]")


def _to_day(date_str):
    try:
        return datetime.strptime(str(date_str)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc).date()
    except (ValueError, TypeError):
        return None


def stale_current_claims(body, sources, today, max_age_days=None):
    """Deterministic staleness check (no LLM): the present-state claims in `body` whose every
    cited `[N]` source predates `today` by more than `max_age_days`. A claim grounded only on
    stale sourcing is the freshness failure (a since-departed manager briefed as current) that a
    semantic auditor misses when an old source genuinely "supports" the past-true statement."""
    max_age_days = STALE_CLAIM_DAYS if max_age_days is None else max_age_days
    today_d = _to_day(today)
    if today_d is None:
        return []
    pub = {}
    for s in sources or []:
        n, p = s.get("n"), _to_day((s.get("published") or "")[:10])
        if n is not None and p is not None:
            pub[int(n)] = p
    flagged = []
    for sent in _SENTENCE.findall(body or ""):
        low = sent.lower()
        if not any(m in low for m in _PRESENT_MARKERS):
            continue
        dated = [pub[int(c)] for c in _CITE.findall(sent) if int(c) in pub]
        if dated and all((today_d - d).days > max_age_days for d in dated):
            flagged.append(sent.strip())
    return flagged


# --------------------------------------------------------------------------- cross-domain links

_INACTIVE = {"dormant", "resolved"}


def cross_domain_links(seed_node_id, now=None, limit=None):
    """The active, engaged neighbours of a survivor's seed node, ranked by decayed engagement.
    Graph math picks the connections; the synthesis prompt hands them to the LLM to articulate.
    Returns `[{label, edge}]`."""
    import time
    now = int(time.time()) if now is None else now
    limit = LINK_LIMIT if limit is None else limit
    if not seed_node_id:
        return []
    out = []
    for e in pg.neighbors_undirected(seed_node_id):
        node = pg.get_node(e["other_id"])
        if not node or node.get("state") in _INACTIVE:
            continue
        eng = pg.engagement_now(node, now)
        if eng < LINK_ENGAGEMENT_FLOOR:
            continue
        out.append({"label": node["label"], "edge": e["type"], "engagement": eng})
    out.sort(key=lambda x: x["engagement"], reverse=True)
    return [{"label": o["label"], "edge": o["edge"]} for o in out[:limit]]


def connections_block(owner, links):
    """A synthesis-prompt fragment naming a survivor's graph neighbours, or "" when there are
    none. The LLM draws the cross-domain link only if the briefing genuinely touches them."""
    if not links:
        return ""
    named = "; ".join(f"{l['label']} ({l['edge'].replace('_', ' ')})" for l in links)
    return (f"\n\nConnections in {owner}'s world (draw the link only if the briefing genuinely "
            f"touches them): {named}")


# --------------------------------------------------------------------------- survivor -> topic

def survivors_to_topics(chosen):
    """Map the Analyst's ranked survivors to the `research_topic` topic shape
    `{title, angle, query, interest, seed_node_id, url}`. `interest` is the seed node's label so
    the run loop's per-interest spread cap still applies; `seed_node_id` carries the graph link
    through to cross-domain synthesis."""
    topics = []
    for c in chosen:
        seed = c.get("seed_node_id")
        node = pg.get_node(seed) if seed else None
        interest = (node["label"] if node else c.get("source")) or ""
        finding = c.get("finding_text") or ""
        cls = c.get("classification") or {}
        angle = (cls.get("action") or finding or "").strip()
        title = (c.get("title") or finding[:80]).strip()
        topics.append({"title": title, "angle": angle[:240],
                       "query": (c.get("title") or finding).strip(),
                       "interest": interest, "seed_node_id": seed, "url": c.get("url")})
    return topics
