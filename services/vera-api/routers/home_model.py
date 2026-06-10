"""Home model router — learn the house's behavioral source code, make it inspectable.

`POST /home/model/refresh` re-mines the full-fidelity home_events window with the
deterministic miners, cross-references HA's automations/scripts (read LIVE over the HA config
API — verified to return full triggers/actions) to tag each pattern already-automated vs emergent
candidate, has Vera narrate the verified specifics (narrate, never invent), and replaces
the stored model generation. `GET /home/model` is the inspectable "here's what your house does."

Understand-only: no actuation, no optimization, no alerting (those layer on later).
"""
import asyncio
import json
import os
import time
from collections import defaultdict

import aiohttp
from fastapi import APIRouter

from . import home_events_store as store
from . import home_model_mine as mine
from . import home_model_store as hms
from .pulse import _vera

router = APIRouter()
TZ_NAME = os.environ.get("HOME_TZ", "UTC")
WINDOW_DAYS = int(os.environ.get("HOME_MODEL_WINDOW_DAYS", "30"))

# Keep the model rich but bounded — top-by-score per kind (storage isn't the constraint, but a
# few thousand weak numeric curves would be noise in the inspectable view).
CAPS = {"temporal": 120, "sequence": 120, "conditional": 120, "numeric": 60}
NARRATE_TOP = 50


def _ha() -> tuple[str, str]:
    """(url, token) from the integration registry at call time; empty when disabled."""
    from . import integrations
    cfg = integrations.integration("home_assistant") or {}
    return cfg.get("url", ""), cfg.get("token", "")


async def _ha_get(session, path):
    url, token = _ha()
    try:
        async with session.get(f"{url}{path}", headers={"Authorization": f"Bearer {token}"},
                               timeout=aiohttp.ClientTimeout(total=20)) as r:
            return await r.json() if r.status == 200 else None
    except Exception:
        return None


def _add_eids(val, acc: set):
    if isinstance(val, str):
        acc.add(val)
    elif isinstance(val, list):
        acc.update(v for v in val if isinstance(v, str))


def _collect_entities(node, acc: set):
    """Recursively pull every entity_id out of an action/sequence blob (handles target.entity_id,
    bare entity_id, and nested parallel/choose/sequence script structures)."""
    if isinstance(node, dict):
        tgt = node.get("target")
        if isinstance(tgt, dict):
            _add_eids(tgt.get("entity_id"), acc)
        _add_eids(node.get("entity_id"), acc)
        for v in node.values():
            _collect_entities(v, acc)
    elif isinstance(node, list):
        for v in node:
            _collect_entities(v, acc)


def _trigger_info(cfg):
    trigs = cfg.get("triggers") or cfg.get("trigger") or []
    if isinstance(trigs, dict):
        trigs = [trigs]
    types, ents = set(), set()
    for t in trigs:
        if isinstance(t, dict):
            types.add(t.get("trigger") or t.get("platform"))
            _add_eids(t.get("entity_id"), ents)
    return types, ents


async def _fetch_automations():
    """Live cross-reference data from HA: what each automation/script targets + triggers on."""
    if not _ha()[1]:
        return None
    async with aiohttp.ClientSession() as s:
        states = await _ha_get(s, "/api/states")
        if not states:
            return None
        autos = [e for e in states if e.get("entity_id", "").startswith("automation.")]
        scripts = [e for e in states if e.get("entity_id", "").startswith("script.")]

        async def auto_cfg(e):
            aid = (e.get("attributes") or {}).get("id")
            return (e, await _ha_get(s, f"/api/config/automation/config/{aid}")) if aid else None

        async def script_cfg(e):
            obj = e["entity_id"].split(".", 1)[1]
            return (e, await _ha_get(s, f"/api/config/script/config/{obj}"))

        results = await asyncio.gather(*[auto_cfg(e) for e in autos],
                                       *[script_cfg(e) for e in scripts])

    alias_by_entity = {}        # automation.x / script.y -> human alias
    temporal_targets = {}       # entity -> alias of a time/sun-triggered automation that drives it
    trigger_index = defaultdict(list)  # trigger entity -> [(alias, action_entities set)]
    for item in results:
        if not item or not item[1]:
            continue
        e, cfg = item
        alias = cfg.get("alias") or e["entity_id"]
        alias_by_entity[e["entity_id"]] = alias
        acts = set()
        _collect_entities(cfg.get("actions") or cfg.get("action") or cfg.get("sequence"), acts)
        if e["entity_id"].startswith("automation."):
            ttypes, tents = _trigger_info(cfg)
            if ttypes & {"time", "time_pattern", "sun"}:
                for a in acts:
                    temporal_targets.setdefault(a, alias)
            for te in tents:
                trigger_index[te].append((alias, acts))
    return {"alias_by_entity": alias_by_entity, "temporal_targets": temporal_targets,
            "trigger_index": trigger_index}


def _tag_automation(p, auto, caused):
    """already-automated (context-linked > config-match) vs emergent candidate — biased toward
    candidate, since a false 'automated' tag would hide a real automation opportunity."""
    e = p["entity_id"]
    frac, cause_entity = caused.get(e, (0.0, None))
    if frac >= 0.6 and cause_entity:
        ref = (auto or {}).get("alias_by_entity", {}).get(cause_entity, cause_entity)
        p.update(automated=True, evidence="context-linked", automation_ref=ref)
        return
    if auto:
        if p["kind"] == "temporal" and e in auto["temporal_targets"]:
            p.update(automated=True, evidence="config-match", automation_ref=auto["temporal_targets"][e])
            return
        if p["kind"] == "sequence":
            for alias, acts in auto["trigger_index"].get(e, []):
                if p["peer_id"] in acts:
                    p.update(automated=True, evidence="config-match", automation_ref=alias)
                    return
    p.update(automated=False, evidence=None, automation_ref=None)


def _cap(patterns):
    by = defaultdict(list)
    for p in patterns:
        by[p["kind"]].append(p)
    out = []
    for kind, ps in by.items():
        ps.sort(key=lambda p: -p["score"])
        out += ps[:CAPS.get(kind, 100)]
    return out


async def _narrate(patterns):
    """One grounded LLM pass: restate the top patterns' verified specifics in plain language."""
    top = sorted(patterns, key=lambda p: -p["score"])[:NARRATE_TOP]
    if not top:
        return
    items = [{"i": i, "kind": p["kind"], "entity": p["entity_id"], "peer": p.get("peer_id"),
              "spec": p["spec"], "automated": p["automated"]} for i, p in enumerate(top)]
    sys = (
        "You're describing what a home does, in plain language. You are GIVEN patterns mined "
        "from the real event log and already verified — restate EACH in ONE short, natural sentence. "
        "Invent NOTHING: use only the entities, times, conditions and counts provided; do not add "
        "causes or reasons. If a pattern is automated, you may note it's an existing automation. "
        "Return ONLY a JSON object mapping the index (as a string) to the sentence. No preamble."
    )
    try:
        resp = await _vera([{"role": "system", "content": sys},
                            {"role": "user", "content": json.dumps(items, separators=(",", ":"))}],
                           temperature=0.2)
        j = json.loads(resp[resp.index("{"):resp.rindex("}") + 1])
        for i, p in enumerate(top):
            s = j.get(str(i))
            if isinstance(s, str) and s.strip():
                p["narration"] = s.strip()
    except Exception:
        pass  # narration is decorative; the structured specifics remain the source of truth


async def run_refresh(dry_run: bool = False) -> dict:
    now = int(time.time())
    since = now - WINDOW_DAYS * 86400
    events = store.recent(limit=20_000_000, since=since)  # full window, oldest..newest order doesn't matter
    patterns = _cap(mine.mine_all(events, TZ_NAME))

    auto = await _fetch_automations()
    caused = mine.automation_causation(events)
    for p in patterns:
        _tag_automation(p, auto, caused)

    await _narrate(patterns)

    n_auto = sum(1 for p in patterns if p["automated"])
    by_kind = {}
    for p in patterns:
        by_kind[p["kind"]] = by_kind.get(p["kind"], 0) + 1
    meta = {
        "window_start": since, "window_end": now, "events_scanned": len(events),
        "n_patterns": len(patterns), "n_automated": n_auto, "n_candidate": len(patterns) - n_auto,
    }
    if not dry_run:
        hms.replace_patterns(patterns, now)
        hms.record_run(meta, now)
    return {
        "ok": True, "dry_run": dry_run, "window_days": WINDOW_DAYS,
        "ha_configured": auto is not None, **meta, "by_kind": by_kind,
        "sample": patterns[:5] if dry_run else None,
    }


@router.post("/home/model/refresh", tags=["home"])
async def refresh(dry_run: bool = False):
    return await run_refresh(dry_run=dry_run)


@router.get("/home/model", tags=["home"])
async def model(kind: str | None = None, automated: bool | None = None,
                min_consistency: float | None = None, entity: str | None = None, limit: int = 200):
    return {
        "patterns": hms.query(kind=kind, automated=automated, min_consistency=min_consistency,
                              entity=entity, limit=min(limit, 1000)),
        "run": hms.last_run(),
    }


@router.get("/home/model/stats", tags=["home"])
async def model_stats():
    return {"run": hms.last_run(), "window_days": WINDOW_DAYS, "ha_configured": bool(_ha()[1])}
