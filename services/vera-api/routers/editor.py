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
                       "interest": interest, "seed_node_id": seed, "url": c.get("url"),
                       "scores": c.get("scores")})
    return topics


# --------------------------------------------------------------------------- journal-as-view

_VIEW_TYPES = ("watch", "project")
_DORMANT = {"dormant"}


def _slug(heading):
    return re.sub(r"[^a-z0-9]+", "-", (heading or "").lower()).strip("-") or "entry"


def _origin(node):
    """A node's journal origin ('requested' when the owner asked for the watch, else 'self'),
    read from the marker fact that `author_watch` stamps."""
    for f in node.get("facts") or []:
        if isinstance(f, dict) and f.get("source") == "journal:origin":
            return "requested" if str(f.get("text", "")).lower().startswith("request") else "self"
    return "self"


def _entry_text(node):
    """One journal entry rendered from a node: the `## heading` the Swift view strips, then the
    body it shows (origin, resolve condition, the durable facts, and the next-check date)."""
    lines = [f"## {node['label']}",
             f"Origin: {'you asked' if _origin(node) == 'requested' else 'self-directed'}"]
    if (node.get("resolve_condition") or "").strip():
        lines.append(f"Resolves when: {node['resolve_condition']}")
    for f in (node.get("facts") or [])[:8]:
        t = f.get("text") if isinstance(f, dict) else str(f)
        if t:
            lines.append(f"- {t}")
    nc = node.get("next_check")
    if nc:
        lines.append(f"Next check: {datetime.fromtimestamp(nc, timezone.utc).strftime('%Y-%m-%d')}")
    return "\n".join(lines)


def _archive(resolved_nodes):
    """Resolved watch/project nodes grouped into cold-storage months by when they resolved."""
    by_month = {}
    for n in resolved_nodes:
        month = datetime.fromtimestamp(n.get("updated_at") or 0, timezone.utc).strftime("%Y-%m")
        by_month.setdefault(month, []).append(_entry_text(n))
    return [{"month": m, "text": "\n\n".join(by_month[m])} for m in sorted(by_month, reverse=True)]


def journal_view(now=None):
    """The journal rendered from the Profile Graph's watch/project nodes — the node is the
    source of truth, the document is a view. Active nodes become entries (never-checked first,
    then soonest due); resolved nodes go to the archive. Returns the Swift contract:
    `{ok, entries:[{heading, slug, text, next_check, origin}], raw, archive}`."""
    active, resolved = [], []
    for t in _VIEW_TYPES:
        for n in pg.all_nodes(type=t):
            st = n.get("state")
            if st == "resolved":
                resolved.append(n)
            elif st not in _DORMANT:
                active.append(n)
    active.sort(key=lambda n: (n.get("next_check") is not None, n.get("next_check") or 0))
    entries = [{"heading": n["label"], "slug": _slug(n["label"]), "text": _entry_text(n),
                "next_check": n.get("next_check"), "origin": _origin(n)} for n in active]
    raw = "# Journal\n\n" + "\n\n".join(e["text"] for e in entries)
    return {"ok": True, "entries": entries, "raw": raw, "archive": _archive(resolved)}


def _condition_satisfied(node):
    """Whether a watch's resolve condition has been observed met — recorded as a fact whose
    source begins `resolution` (written by the extraction/feedback path, not authored prose)."""
    return any(isinstance(f, dict) and str(f.get("source", "")).startswith("resolution")
               for f in node.get("facts") or [])


def resolve_due(now=None):
    """Resolve watch nodes by a deterministic state transition: a watch flips to `resolved`
    only when its `resolve_condition` is set, its `next_check` date has passed, AND the
    condition has been observed met. No LLM recheck — the immortal-watch failure cannot recur.
    Returns the ids resolved this pass."""
    import time
    now = int(time.time()) if now is None else now
    resolved = []
    for n in pg.all_nodes(type="watch"):
        if n.get("state") in ("resolved", "dormant"):
            continue
        if not (n.get("resolve_condition") or "").strip():
            continue
        nc = n.get("next_check")
        if nc is None or nc > now:
            continue
        if not _condition_satisfied(n):
            continue
        pg.upsert_node(id=n["id"], type="watch", label=n["label"], aliases=n["aliases"],
                       facts=n["facts"], engagement=n["engagement"], last_engaged=n["last_engaged"],
                       state="resolved", resolve_condition=n["resolve_condition"],
                       next_check=n["next_check"], confidence=n["confidence"], embedding=n["embedding"])
        resolved.append(n["id"])
    return resolved


async def author_watch(label, *, facts=None, resolve_condition=None, next_check=None,
                       origin="self", embedding=None, now=None):
    """Land a standing commitment as a watch node, the graph write path that replaces authored
    prose. Folding is the store's cosine dedup-merge — two unrelated situations stay two nodes,
    so the runaway-accretion failure cannot recur. Returns the node id."""
    import time
    now = int(time.time()) if now is None else now
    fl = []
    for f in facts or []:
        if isinstance(f, dict) and f.get("text"):
            fl.append(f)
        elif isinstance(f, str) and f.strip():
            fl.append(pg.make_fact(f.strip(), source="journal:material", observed_at=now))
    fl.append(pg.make_fact(origin, source="journal:origin", observed_at=now))
    emb = embedding if embedding is not None else await pg.embed(label)
    return pg.merge_or_create(type="watch", label=label, embedding=emb, facts=fl, now=now,
                              state="active", resolve_condition=resolve_condition,
                              next_check=next_check)
