"""Self-reconciling Home map + drift alerts.

A read-mostly nightly pass that keeps the durable Home map (knowledge.db) honest against live reality
(HA /api/states). It:

  * validates the live-source INDEX (`live_source`-typed knowledge entities) — every pointer still
    resolves to real HA entities; episodic sources (e.g. a fermentation monitor) that are idle are NOT faults;
  * detects ENTITY DRIFT — a tracked-domain entity newly appearing (not in the map) or a map-referenced
    entity that's vanished from HA (debounced so a flapping `unavailable` doesn't read as gone);
  * detects STALE FACTS — durable facts overdue for re-verification (`last_verified` convention);
  * AUTO-RESOLVES what's unambiguous (an index pointer that moved to a single clear successor) with no
    human in the loop, and SURFACES the rest as System-vein cards with confirm/re-verify.

Cards name the specific drift deterministically — no LLM narration, nothing invented.
"""

import json
import os
import re

import aiohttp
from fastapi import APIRouter

from . import home_reconcile_store as store
from . import knowledge_store as ks
from .actions import ProposeCard, propose_card
from .home_reconcile_match import classify_index, find_successor, match_entities
from .pulse import _inject

router = APIRouter()

STALE_DAYS = int(os.environ.get("RECONCILE_STALE_DAYS", "180"))


def _ha() -> tuple[str, str]:
    """(url, token) from the integration registry at call time; empty when disabled."""
    from . import integrations
    cfg = integrations.integration("home_assistant") or {}
    return cfg.get("url", ""), cfg.get("token", "")

# Physical-device domains worth carding when a NEW one appears un-mapped. Deliberately excludes the
# high-cardinality noise domains (sensor/binary_sensor/number/button/update/…) — a new humidity sensor
# isn't map drift, a new lock is. Removal detection is NOT limited to these (see _map_entity_refs).
TRACKED_DOMAINS = {
    "climate", "lock", "cover", "fan", "vacuum", "media_player", "light", "switch",
    "water_heater", "humidifier", "alarm_control_panel", "camera", "lawn_mower", "valve",
}

# Valid HA entity-id domains — used to recognise an entity_id appearing as a value inside a map fact.
HA_DOMAINS = TRACKED_DOMAINS | {
    "sensor", "binary_sensor", "person", "device_tracker", "automation", "script", "scene",
    "input_boolean", "input_number", "input_select", "input_text", "number", "select", "button",
    "update", "weather", "sun", "zone", "remote", "siren", "todo", "calendar", "image", "text",
}
_ENTITY_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")

# Knowledge types the reconciler must NOT treat as human-authored durable facts for staleness:
# live_source pointers are re-verified by the reconciler itself, not by a person.
NON_DURABLE_TYPES = {"live_source"}


async def _fetch_states() -> list:
    url, token = _ha()
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{url}/api/states", headers={"Authorization": f"Bearer {token}"},
                         timeout=aiohttp.ClientTimeout(total=30)) as r:
            return await r.json()


def _map_entity_refs(index: list) -> set:
    """Every HA entity_id the durable map points at — scanned from knowledge fact attrs plus the
    pinned `entity` pointers in the live-source index. Defines what counts as 'a map entity'."""
    refs = set()
    for ls in index:
        m = (ls.get("attrs") or {}).get("match") or {}
        if m.get("by") == "entity" and m.get("value"):
            refs.add(m["value"])
    for e in ks.query(limit=100000):
        for v in (e.get("attrs") or {}).values():
            if isinstance(v, str) and _ENTITY_RE.match(v) and v.split(".")[0] in HA_DOMAINS:
                refs.add(v)
    return refs


def _load_index() -> list:
    return ks.query(type="live_source", limit=1000)


# ---- card helpers ----------------------------------------------------------

async def _notice_card(title, body, severity, category, summary=None):
    res = await _inject(title, body, summary=summary, kind="status",
                        severity=severity, category=category)
    return res.get("id")


# ---- the reconcile pass ----------------------------------------------------

async def run_reconcile(dry_run: bool = False) -> dict:
    if not _ha()[1]:
        return {"ok": False, "error": "home_assistant integration not enabled"}

    states = await _fetch_states()
    current_meta = {}
    for e in states:
        eid = e.get("entity_id")
        if not eid or "." not in eid:
            continue
        current_meta[eid] = {
            "domain": eid.split(".")[0], "integration": None,
            "friendly_name": (e.get("attributes") or {}).get("friendly_name") or eid,
        }

    index = _load_index()
    map_refs = _map_entity_refs(index)

    active_keys = set()       # every drift condition true RIGHT NOW (carded or not) — for resolve_absent
    would_card = []           # dry-run preview of what would be carded
    auto_resolved = []
    index_report = []
    stats = {"states_seen": len(current_meta), "index_total": len(index),
             "index_ok": 0, "index_failed": 0, "index_idle": 0,
             "new_n": 0, "removed_n": 0, "stale_n": 0, "auto_resolved": 0, "carded": 0}

    async def card_once(key, kind, ref, detail, *, title, body, severity, category, action=None):
        """Card a drift exactly once (dedup via the ledger). Returns nothing; updates stats."""
        active_keys.add(key)
        if dry_run:
            would_card.append({"key": key, "kind": kind, "ref": ref, "detail": detail})
            return
        is_new = store.record_drift(key, kind, ref, detail)
        if not is_new:
            return
        if action:
            r = await propose_card(ProposeCard(verb=action["verb"], args=action["args"],
                                               title=title, body=body, source="reconcile",
                                               actor="vera", kind="status", severity=severity,
                                               category=category))
            card_id = r.get("card_id")
        else:
            card_id = await _notice_card(title, body, severity, category, summary=detail)
        if card_id:
            store.set_card(key, card_id)
        stats["carded"] += 1

    # --- pass 1: live-source index --------------------------------------------------------------
    for ls in index:
        a = ls.get("attrs") or {}
        m = a.get("match") or {}
        kind = a.get("kind", "continuous")
        expected = int(a.get("expected_min", 1) or 0)
        resolved = match_entities(states, m)
        n = len(resolved)
        succ = find_successor(m["value"], states) if (n == 0 and m.get("by") == "entity") else None
        status = classify_index(n, kind, expected, can_succeed=succ is not None)
        entry = {"id": ls["id"], "question": a.get("question") or ls.get("name"),
                 "by": m.get("by"), "value": m.get("value"), "kind": kind,
                 "expected_min": expected, "matched": n, "sample": resolved[:3], "status": status}

        if status in ("active", "ok"):
            stats["index_ok"] += 1
        elif status == "idle":          # episodic source not currently active — NOT a fault
            stats["index_idle"] += 1
        elif status == "auto_resolve":  # pinned pointer moved to a single clear successor
            entry["successor"] = succ
            auto_resolved.append({"id": ls["id"], "from": m["value"], "to": succ})
            stats["index_ok"] += 1
            stats["auto_resolved"] += 1
            if not dry_run:
                p = ks.propose("set", entity_id=ls["id"], type=ls["type"], name=ls["name"],
                               attrs={"match": {**m, "value": succ}}, source="reconcile", actor="vera")
                ks.commit(p["token"])
        elif status == "unresolved":
            stats["index_failed"] += 1
            await card_once(
                f"index:{ls['id']}", "index_unresolved", ls["id"],
                f"{entry['question']}: pointer {m.get('by')}={m.get('value')!r} resolves to 0 entities",
                title="Home map: a live-source pointer no longer resolves",
                body=(f"**{entry['question']}** points at `{m.get('by')}={m.get('value')}` "
                      f"(integration `{a.get('integration')}`), which matches no live HA entity. "
                      "The source may have been renamed or removed. Update the map pointer."),
                severity="alert", category="infra")
        elif status == "degraded":
            stats["index_failed"] += 1
            await card_once(
                f"index_degraded:{ls['id']}", "index_degraded", ls["id"],
                f"{entry['question']}: {n} entities, expected ≥{expected}",
                title="Home map: a live source is thinner than expected",
                body=(f"**{entry['question']}** (`{a.get('integration')}`) resolves to {n} entities, "
                      f"below the expected minimum of {expected}. Part of the integration may be down "
                      "or removed."),
                severity="notice", category="infra")
        index_report.append(entry)

    # --- pass 2: entity drift (new / removed) ---------------------------------------------------
    diff = store.preview_diff(current_meta) if dry_run else store.apply_diff(current_meta)
    new_entities = [e for e in diff["new"] if e["domain"] in TRACKED_DOMAINS]
    removed_entities = [e for e in diff["removed"] if e["entity_id"] in map_refs]
    stats["new_n"] = len(new_entities)
    stats["removed_n"] = len(removed_entities)

    for e in new_entities:
        await card_once(
            f"new:{e['entity_id']}", "new_entity", e["entity_id"],
            f"new {e['domain']}: {e['friendly_name']}",
            title="New device not in the Home map",
            body=(f"A new `{e['domain']}` entity appeared in Home Assistant: "
                  f"**{e['friendly_name']}** (`{e['entity_id']}`). It isn't in the map yet. "
                  "Add it if it's something worth tracking, or dismiss."),
            severity="notice", category="infra")
    for e in removed_entities:
        await card_once(
            f"removed:{e['entity_id']}", "removed_entity", e["entity_id"],
            f"mapped entity gone: {e['entity_id']}",
            title="A mapped entity has vanished from Home Assistant",
            body=(f"**{e['friendly_name']}** (`{e['entity_id']}`) is referenced by the Home map but "
                  f"has been absent from HA for {store.REMOVAL_THRESHOLD} consecutive checks. It may "
                  "have been removed or renamed. Update the map."),
            severity="alert", category="infra")

    # --- pass 3: stale durable facts ------------------------------------------------------------
    stale = [s for s in ks.stale_by_last_verified(STALE_DAYS)
             if s["type"] not in NON_DURABLE_TYPES]
    stats["stale_n"] = len(stale)
    for s in stale:
        await card_once(
            f"stale:{s['id']}", "stale_fact", s["id"],
            f"{s['type']} '{s['name']}' last verified {s['age_days']}d ago ({s['basis']})",
            title="A home fact is overdue for re-verification",
            body=(f"**{s['name']}** ({s['type']}) was last verified {s['age_days']} days ago "
                  f"(by {s['basis']}). Confirm it's still accurate to re-stamp it."),
            severity="notice", category="vera",
            action={"verb": "knowledge.reverify", "args": {"entity_id": s["id"]}})

    resolved_drifts = []
    if not dry_run:
        resolved_drifts = store.resolve_absent(active_keys)
        store.record_run(dry_run, stats)

    out = {"ok": True, "dry_run": dry_run, **stats,
           "index": index_report, "auto_resolved": auto_resolved,
           "new_entities": new_entities, "removed_entities": removed_entities,
           "stale_facts": stale, "first_run": diff.get("first_run", False),
           "resolved_drifts": resolved_drifts}
    if dry_run:
        out["would_card"] = would_card
    return out


# ---- index seeding ---------------------------------------------------------

# The live-source index seeds are deployment config, never code — each entry maps a household
# question to the HA integration/entities that answer it live. Provide a JSON array inline via
# HOME_LIVE_SOURCE_INDEX or as a file via HOME_LIVE_SOURCE_INDEX_PATH (e.g. /data/live_sources.json):
#   [{"question": "Server CPU / RAM / disk", "integration": "unraid",
#     "match": {"by": "name_contains", "value": "myserver_"}, "kind": "continuous", "expected_min": 50}, ...]
# Matchers are PROVISIONAL — validate against a dry-run and refine `expected_min`/`value` to real
# entity names before enabling the schedule.
def _seed_index() -> list[dict]:
    raw = os.environ.get("HOME_LIVE_SOURCE_INDEX", "").strip()
    if not raw:
        path = os.environ.get("HOME_LIVE_SOURCE_INDEX_PATH", "").strip()
        if path:
            try:
                raw = open(path, encoding="utf-8").read()
            except OSError:
                return []
    try:
        seeds = json.loads(raw) if raw else []
        return seeds if isinstance(seeds, list) else []
    except ValueError:
        return []


SEED_INDEX = _seed_index()


@router.post("/home/reconcile/seed_index", tags=["home"])
async def seed_index():
    """Idempotent: create the live_source index entities from the configured seed list (if absent)."""
    if not SEED_INDEX:
        return {"ok": False, "configured": False,
                "error": "no live-source index configured. Set HOME_LIVE_SOURCE_INDEX or HOME_LIVE_SOURCE_INDEX_PATH"}
    created, existing = [], []
    for seed in SEED_INDEX:
        eid = f"live_source:{re.sub(r'[^a-z0-9]+', '-', seed['question'].lower()).strip('-')}"
        if ks.get(eid):
            existing.append(eid)
            continue
        p = ks.propose("set", type="live_source", name=seed["question"],
                       attrs={k: v for k, v in seed.items() if k != "question"} | {"question": seed["question"],
                              "source": "live-source index config"},
                       source="reconcile", actor="vera")
        ks.commit(p["token"])
        created.append(eid)
    return {"ok": True, "created": created, "existing": existing}


@router.post("/home/reconcile/run", tags=["home"])
async def run(dry_run: bool = False):
    return await run_reconcile(dry_run=dry_run)


@router.get("/home/reconcile", tags=["home"])
async def get_state():
    return {"last_run": store.last_run(), "open_drift": store.list_open()}
