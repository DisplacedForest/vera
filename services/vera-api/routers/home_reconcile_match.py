"""Pure live-state matchers for the reconciler. No deps — unit-tested standalone.

Kept separate from home_reconcile.py (which imports FastAPI/aiohttp) so the resolution logic — the
part that decides whether an index pointer still resolves and whether a vanished entity has an
unambiguous successor — can be tested without any network or web-framework context.
"""

import re
from fnmatch import fnmatch


def match_entities(states: list, match: dict) -> list:
    """Resolve a live_source `match` against live HA states. by ∈ {entity, glob, name_contains}.

    * entity        — exact entity_id.
    * glob          — fnmatch against entity_id (e.g. "sensor.unraid_*").
    * name_contains — substring of entity_id OR friendly_name (case-insensitive).
    """
    by, val = match.get("by"), (match.get("value") or "")
    out = []
    for e in states:
        eid = e.get("entity_id")
        if not eid:
            continue
        fn = (e.get("attributes") or {}).get("friendly_name") or ""
        if by == "entity":
            if eid == val:
                out.append(eid)
        elif by == "glob":
            if fnmatch(eid, val):
                out.append(eid)
        elif by == "name_contains":
            v = val.lower()
            if v and (v in eid.lower() or v in fn.lower()):
                out.append(eid)
    return out


def classify_index(matched_n: int, kind: str, expected_min: int, can_succeed: bool) -> str:
    """Decide a live-source pointer's status from its live resolution. The episodic branch is the
    crux of the 'idle ≠ fault' guarantee: an episodic source with zero live entities is *idle*
    (e.g. a fermentation monitor with no active batch), never a failure.

    Returns one of: active | idle | ok | degraded | auto_resolve | unresolved.
    """
    if kind == "episodic":
        return "active" if matched_n > 0 else "idle"
    if matched_n == 0:
        return "auto_resolve" if can_succeed else "unresolved"
    if matched_n < expected_min:
        return "degraded"
    return "ok"


def find_successor(eid: str, states: list):
    """For a vanished pinned entity, return the single obvious successor — same domain, same object
    base modulo a trailing `_N` rename suffix (HA's standard rename collision pattern) — else None.
    Conservative by design: zero or multiple candidates → None (don't auto-resolve an ambiguity)."""
    if "." not in eid:
        return None
    dom, obj = eid.split(".", 1)
    base = re.sub(r"_\d+$", "", obj)
    cands = []
    for s in states:
        sid = s.get("entity_id", "")
        if not sid or sid == eid or "." not in sid:
            continue
        sd, so = sid.split(".", 1)
        if sd == dom and re.sub(r"_\d+$", "", so) == base and so != obj:
            cands.append(sid)
    return cands[0] if len(cands) == 1 else None
