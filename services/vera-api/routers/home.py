"""Home rhythm router — learn HA usage baselines, surface deviations.

Nightly `POST /home/digest` folds yesterday's HA history into rhythm_store and posts a "Home rhythm"
Pulse card. `GET /home/deviations` is a live read the heartbeat folds into each tick. `GET /home/rhythm`
returns an entity's learned baseline. Reuses the HA pattern from heartbeat.py and the pulse helpers;
all pure math lives in rhythm_store.py.
"""
import json
import os
from datetime import datetime, time, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import aiohttp
from fastapi import APIRouter

from . import rhythm_store as rs
from .pulse import _inject, _vera
from .persona import owner, voiced

router = APIRouter()
TZ = ZoneInfo(os.environ.get("HOME_TZ", "UTC"))


def _ha() -> tuple[str, str]:
    """(url, token) from the integration registry at call time; empty when disabled."""
    from . import integrations
    cfg = integrations.integration("home_assistant") or {}
    return cfg.get("url", ""), cfg.get("token", "")

# binary_sensor device classes that carry rhythm signal (everything else is noise here)
BINARY_CLASSES = {"motion", "occupancy", "presence", "door", "window", "garage_door", "opening"}

# domain -> predicate defining "active", and the human label stored in entity_meta
DOMAIN_ACTIVE = {
    "binary_sensor": lambda s: s == "on",
    "person": lambda s: s == "home",
    "device_tracker": lambda s: s == "home",
    "light": lambda s: s == "on",
    "switch": lambda s: s == "on",
    "fan": lambda s: s == "on",
    "media_player": lambda s: s == "playing",
    "climate": lambda s: s not in ("off", "unavailable", "unknown", ""),
    "lock": lambda s: s == "unlocked",
    "cover": lambda s: s == "open",
}
ACTIVE_DEF = {
    "binary_sensor": "on", "person": "home", "device_tracker": "home", "light": "on",
    "switch": "on", "fan": "on", "media_player": "playing", "climate": "not off",
    "lock": "unlocked", "cover": "open",
}


async def classify() -> dict:
    """{entity_id: {domain, friendly_name, active_def}} for tracked, rhythm-relevant entities."""
    url, token = _ha()
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{url}/api/states", headers={"Authorization": f"Bearer {token}"},
                         timeout=aiohttp.ClientTimeout(total=20)) as r:
            states = await r.json()
    out = {}
    for e in states:
        eid = e.get("entity_id", "")
        dom = eid.split(".")[0]
        if dom not in DOMAIN_ACTIVE:
            continue
        a = e.get("attributes") or {}
        if dom == "binary_sensor" and a.get("device_class") not in BINARY_CLASSES:
            continue
        out[eid] = {"domain": dom, "friendly_name": a.get("friendly_name", eid), "active_def": ACTIVE_DEF[dom]}
    return out


async def history(entity_ids, start: datetime, end: datetime) -> dict:
    """{entity_id: [(iso_ts, state), ...]} over [start, end) via HA's history API."""
    if not entity_ids:
        return {}
    base, token = _ha()
    url = f"{base}/api/history/period/{quote(start.isoformat())}"
    params = {"end_time": end.isoformat(), "filter_entity_id": ",".join(entity_ids),
              "minimal_response": "", "no_attributes": ""}
    async with aiohttp.ClientSession() as s:
        async with s.get(url, headers={"Authorization": f"Bearer {token}"}, params=params,
                         timeout=aiohttp.ClientTimeout(total=90)) as r:
            data = await r.json()
    out = {}
    for series in (data or []):
        if not series:
            continue
        eid = series[0].get("entity_id")
        if not eid:
            continue
        pts = [(p.get("last_changed") or p.get("last_updated"), p.get("state")) for p in series
               if (p.get("last_changed") or p.get("last_updated"))]
        out[eid] = pts
    return out


async def compute_deviations() -> list:
    """Live deviations: for the current hour, which entities are off-baseline right now."""
    if not _ha()[1]:
        return []
    try:
        meta = await classify()
    except Exception:
        return []
    now = datetime.now(TZ)
    dow, hour, today = now.weekday(), now.hour, now.date().isoformat()
    start = now - timedelta(hours=3)
    try:
        hist = await history(list(meta), start, now)
    except Exception:
        return []
    devs = []
    for eid, m in meta.items():
        pred = DOMAIN_ACTIVE[m["domain"]]
        active_now = hour in rs.hours_active(hist.get(eid, []), pred, TZ, today)
        p, observed = rs.prob(eid, dow, hour)
        d = rs.detect(eid, p, observed, active_now)
        if d:
            d["name"] = m["friendly_name"]
            devs.append(d)
    return devs


async def run_digest(dry_run: bool = False) -> dict:
    """Nightly: fold yesterday into the baselines, then narrate + post a 'Home rhythm' card."""
    if not _ha()[1]:
        return {"ok": False, "error": "home_assistant integration not enabled"}
    meta = await classify()
    yest = datetime.now(TZ).date() - timedelta(days=1)
    day_iso = yest.isoformat()
    dow = yest.weekday()
    start = datetime.combine(yest, time.min, tzinfo=TZ)
    end = start + timedelta(days=1)
    hist = await history(list(meta), start, end)

    active_by_entity = {}
    for eid, m in meta.items():
        pred = DOMAIN_ACTIVE[m["domain"]]
        active_by_entity[eid] = rs.hours_active(hist.get(eid, []), pred, TZ, day_iso)

    # What was unusual yesterday — measured against the baseline learned from PRIOR days.
    # Compute BEFORE folding yesterday in, so a day's own anomaly isn't averaged into its own baseline.
    notable = []
    for eid, m in meta.items():
        ah = active_by_entity[eid]
        for h in range(24):
            p, observed = rs.prob(eid, dow, h)
            d = rs.detect(eid, p, observed, h in ah)
            if d:
                d["name"] = m["friendly_name"]
                d["hour"] = h
                notable.append(d)

    # Dry run is read-only: never mutate the baseline or the ingest log.
    recorded = False if dry_run else rs.record_day(day_iso, active_by_entity, meta)

    sys = (
        f"Summarize yesterday's home rhythm for {owner()}. You are given statistical "
        "deviations from the learned baseline (NOT raw events). Write a SHORT, plain-language note "
        "(2-5 bullets) on what was notable or unusual. If the list is empty, reply with EXACTLY: SKIP. "
        "GitHub markdown, no preamble."
    )
    usr = f"Date: {day_iso}\nDeviations (name, kind, hour, baseline prob, observed days):\n" + (
        "\n".join(
            f"- {d['name']}: {d['kind']} at {d['hour']:02d}:00 (p={d['p']}, n={int(d['observed'])})"
            for d in notable
        ) or "(none)"
    )
    body = (await _vera([{"role": "system", "content": voiced(sys)}, {"role": "user", "content": usr}])).strip()

    out = {"ok": True, "day": day_iso, "recorded": recorded, "entities": len(meta),
           "deviations": len(notable), "posted": False, "body": body}
    if dry_run or body.upper().startswith("SKIP") or len(body) < 10:
        return out
    rs.save_digest(day_iso, body, json.dumps(notable))
    await _inject("Home rhythm", body,
                  summary=f"{len(notable)} deviation(s) from your usual {day_iso} rhythm")
    out["posted"] = True
    return out


@router.post("/home/digest", tags=["home"])
async def digest(dry_run: bool = False):
    return await run_digest(dry_run=dry_run)


@router.get("/home/deviations", tags=["home"])
async def deviations():
    return {"deviations": await compute_deviations()}


@router.get("/home/rhythm", tags=["home"])
async def rhythm(entity: str):
    b = rs.baseline(entity)
    return {
        "entity": entity,
        "buckets": [
            {"dow": d, "hour": h, "p": round(p, 3), "observed": o}
            for (d, h), (p, o) in sorted(b.items())
        ],
    }
