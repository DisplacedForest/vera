"""Agentic activity feed — one normalized, newest-first list of everything Vera does
on her own, merged from the heartbeat outcome log, the action audit log, the
scheduler run log, and OWUI automation runs.

Event shape: {ts, source: scheduler|heartbeat|action|owui, kind, title, detail,
tool?, ref?}. `tool` carries attribution when known (action verb, producer job id).
Each source contributes independently: a missing store or an unreachable OWUI yields
an empty contribution, never an error.
"""
import logging
import os
import time

import aiohttp
from fastapi import APIRouter, Query

from . import action_store, heartbeat_store, scheduler_store

log = logging.getLogger("agentic")
router = APIRouter()

OWUI_BASE = os.environ.get("OWUI_BASE", "").rstrip("/")
OWUI_KEY = os.environ.get("OWUI_KEY", "")

# Human titles for heartbeat outcome kinds; unknown kinds fall back to the kind itself.
_HEARTBEAT_TITLES = {
    "learn": "Studied the house",
    "propose": "Proposed an action",
    "refine": "Refined a proposal",
    "watch": "Watching a situation",
    "confirmed": "Proposal confirmed",
    "dismissed": "Proposal dismissed",
    "foryou": "Surfaced a For You item",
    "foryou_skip": "Considered a For You item, skipped it",
}


def _heartbeat_events(hours: int) -> list[dict]:
    out = []
    for o in heartbeat_store.recent(hours):
        out.append({
            "ts": float(o["ts"]),
            "source": "heartbeat",
            "kind": o["kind"],
            "title": _HEARTBEAT_TITLES.get(o["kind"], o["kind"]),
            "detail": o.get("detail") or "",
            "tool": None,
            "ref": None,
        })
    return out


def _action_events(hours: int) -> list[dict]:
    out = []
    for a in action_store.recent(hours):
        # Lifecycle statuses become the event kind; executed rows keep their lane
        # (auto = free lane, gated = confirmed) so the feed shows how each action ran.
        if a["status"] in ("proposed", "dismissed"):
            kind = a["status"]
        else:
            kind = "auto" if a["auto"] else "gated"
        who = (f" by {a['actor']}" if a["actor"] else "") + (f" via {a['source']}" if a["source"] else "")
        args = ", ".join(f"{k}={v}" for k, v in (a["args"] or {}).items())
        detail = a["status"] + who + (f": {args}" if args else "")
        out.append({
            "ts": float(a["ts"]),
            "source": "action",
            "kind": kind,
            "title": a["verb"] or "action",
            "detail": detail[:300],
            "tool": a["verb"],
            "ref": a["token"],
        })
    return out


def _scheduler_events(hours: int) -> list[dict]:
    from .scheduler import REGISTRY
    out = []
    for r in scheduler_store.recent_runs(hours):
        label = REGISTRY[r["job_id"]][0] if r["job_id"] in REGISTRY else r["job_id"]
        out.append({
            "ts": float(r["ts"]),
            "source": "scheduler",
            "kind": "ok" if r["ok"] else "fail",
            "title": label,
            "detail": (r["detail"] or "")[:300],
            "tool": r["job_id"],
            "ref": None,
        })
    return out


async def _owui_events(hours: int, cutoff: float) -> list[dict]:
    """Automation runs from OWUI's API. While OWUI's automations engine is disabled
    these endpoints refuse, which reads as an empty contribution."""
    if not OWUI_BASE or not OWUI_KEY:
        return []
    headers = {"Authorization": f"Bearer {OWUI_KEY}"}
    timeout = aiohttp.ClientTimeout(total=10)
    out = []
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{OWUI_BASE}/api/v1/automations/list",
                         headers=headers, timeout=timeout) as r:
            if r.status != 200:
                return []
            automations = (await r.json()).get("items") or []
        for a in automations:
            prompt = ((a.get("data") or {}).get("prompt") or "")[:300]
            async with s.get(f"{OWUI_BASE}/api/v1/automations/{a['id']}/runs",
                             headers=headers, timeout=timeout) as r:
                if r.status != 200:
                    continue
                runs = await r.json()
            for run in runs:
                ts = float(run.get("created_at") or 0) / 1e9  # OWUI stamps nanoseconds
                if ts <= cutoff:
                    continue
                out.append({
                    "ts": ts,
                    "source": "owui",
                    "kind": run.get("status") or "unknown",
                    "title": a.get("name") or "OWUI automation",
                    "detail": run.get("error") or prompt,
                    "tool": "owui.automation",
                    "ref": run.get("chat_id"),
                })
    return out


@router.get("/agentic/activity", tags=["agentic"])
async def activity(hours: int = Query(24, ge=1, le=168)):
    cutoff = time.time() - hours * 3600
    events: list[dict] = []
    for name, collect in (("heartbeat", _heartbeat_events),
                          ("action", _action_events),
                          ("scheduler", _scheduler_events)):
        try:
            events.extend(collect(hours))
        except Exception as e:  # noqa: BLE001 — one bad source must never empty the feed
            log.warning("activity source %s failed: %s", name, e)
    try:
        events.extend(await _owui_events(hours, cutoff))
    except Exception as e:  # noqa: BLE001
        log.warning("activity source owui failed: %s", e)
    events.sort(key=lambda e: e["ts"], reverse=True)
    return {"hours": hours, "events": events}
