"""Agentic activity feed and canvas graph — what Vera does on her own, as data.

The activity feed is one normalized, newest-first list of autonomous events, merged
from the heartbeat outcome log, the action audit log, the scheduler run log, and OWUI
automation runs. Event shape: {ts, source: scheduler|heartbeat|action|owui, kind,
title, detail, tool?, ref?}. `tool` carries attribution when known (action verb,
producer job id). Each source contributes independently: a missing store or an
unreachable OWUI yields an empty contribution, never an error.

The graph is the canvas manifest: every flow (scheduler job + the heartbeat), the
surface each one feeds, and per-flow presentation/topology metadata. Declarative in
the vein-catalog spirit — the app renders whatever this says; a new capability is a
new entry here, never an app release. This is also the reserved editor lane: a
server-declared graph can later accept mutations.
"""
import logging
import os
import time
from datetime import datetime

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


# ---- the canvas graph ------------------------------------------------------------------

# The surfaces autonomous work lands on. `stat` is filled live per request.
SURFACES: list[dict] = [
    {"id": "pulse_feed", "label": "Pulse feed", "icon": "newspaper"},
    {"id": "veins", "label": "Veins", "icon": "drop"},
    {"id": "memory", "label": "Memory", "icon": "archivebox"},
    {"id": "actions", "label": "Actions", "icon": "bolt"},
]

_PULSE_STAGES = [
    {"id": "triage", "label": "Triage", "icon": "globe", "tint": "accent"},
    {"id": "gates", "label": "Gates", "icon": "line.3.horizontal.decrease.circle", "tint": "orange"},
    {"id": "synthesis", "label": "Synthesis", "icon": "sparkles", "tint": "purple"},
    {"id": "claim_audit", "label": "Claim audit", "icon": "checkmark.shield", "tint": "cyan"},
    {"id": "cover_art", "label": "Cover art", "icon": "photo", "tint": "purple"},
    {"id": "inject", "label": "Inject", "icon": "arrow.down.to.line", "tint": "green"},
]

# Branch ids double as the heartbeat outcome kinds they report on (see _BRANCH_OF).
_HEARTBEAT_BRANCHES = [
    {"id": "learn", "label": "Learn", "icon": "sparkles", "tint": "accent", "feeds": ["memory"]},
    {"id": "refine", "label": "Refine", "icon": "doc.text", "tint": "purple", "feeds": []},
    {"id": "propose", "label": "Propose", "icon": "bolt", "tint": "orange", "feeds": ["actions"]},
    {"id": "watch", "label": "Watches", "icon": "waveform.path.ecg", "tint": "cyan", "feeds": ["veins"]},
    {"id": "foryou", "label": "For you", "icon": "heart", "tint": "red", "feeds": ["pulse_feed"]},
]

_BRANCH_OF = {
    "learn": "learn", "refine": "refine",
    "propose": "propose", "confirmed": "propose", "dismissed": "propose",
    "watch": "watch",
    "foryou": "foryou", "foryou_skip": "foryou",
}

# Per-flow canvas face: presentation label (the registry label stays the formal name),
# icon/tint (SF Symbol + the app's chart-palette tint names), thematic group, the
# surfaces the flow feeds, and the tools it is known to use (static attribution; the
# activity feed adds per-event attribution on top). Flows with `stages` drill in;
# `stage_layout` is "pipeline" (linear) or "fan" (branches).
FLOW_FACE: dict[str, dict] = {
    "pulse":          {"label": "Pulse briefing", "icon": "newspaper", "tint": "accent",
                       "group": "Ambient", "feeds": ["pulse_feed"],
                       "tools": ["websearch", "vera-image"],
                       "stage_layout": "pipeline", "stages": _PULSE_STAGES},
    "vein_weather":   {"label": "Weather vein", "icon": "cloud.sun", "tint": "cyan",
                       "group": "Ambient", "feeds": ["veins"], "tools": []},
    "signals":        {"label": "Signals check", "icon": "antenna.radiowaves.left.and.right",
                       "tint": "orange", "group": "Ambient", "feeds": ["veins"],
                       "tools": ["websearch"]},
    "memory_groom":   {"label": "Memory groom", "icon": "archivebox", "tint": "purple",
                       "group": "Memory", "feeds": ["memory"], "tools": []},
    "home_model":     {"label": "Home model", "icon": "house", "tint": "cyan",
                       "group": "Home", "feeds": ["actions"], "tools": []},
    "home_reconcile": {"label": "Map reconcile", "icon": "checklist", "tint": "cyan",
                       "group": "Home", "feeds": ["veins"], "tools": []},
    "home_digest":    {"label": "Rhythm digest", "icon": "doc.text", "tint": "cyan",
                       "group": "Home", "feeds": ["veins"], "tools": []},
    "heartbeat":      {"label": "Heartbeat", "icon": "heart", "tint": "accent",
                       "group": "Heartbeat", "feeds": ["pulse_feed", "veins", "memory", "actions"],
                       "tools": ["websearch"],
                       "stage_layout": "fan", "stages": _HEARTBEAT_BRANCHES},
    "vein_status":    {"label": "System vein", "icon": "waveform.path.ecg", "tint": "green",
                       "group": "System", "feeds": ["veins"], "tools": []},
    "vein_media":     {"label": "Media vein", "icon": "film", "tint": "red",
                       "group": "Media", "feeds": ["veins"], "tools": ["overseerr"]},
    "conversation_extract": {"label": "Conversation extraction", "icon": "text.bubble",
                             "tint": "purple", "group": "Memory", "feeds": ["memory"], "tools": []},
    "weight_fit":     {"label": "Weight fit", "icon": "chart.xyaxis.line", "tint": "purple",
                       "group": "Memory", "feeds": ["memory"], "tools": []},
}

# A job with no authored face still renders (and the test suite flags the omission).
_DEFAULT_FACE = {"icon": "clock", "tint": "gray", "group": "Other", "feeds": [], "tools": []}


def _pulse_stage_state() -> dict | None:
    """Distilled last-run record for the pulse pipeline, from the structured run status."""
    from . import pulse_store
    st = pulse_store.get_run_status()
    if st.get("state") in (None, "idle"):
        return None
    rounds = st.get("rounds") or []
    errors = st.get("errors") or []
    return {
        "state": st.get("state"),
        "rounds": len(rounds),
        "proposed": sum(len(r.get("proposed") or []) for r in rounds),
        "gates": st.get("gates") or {},
        "injected": len(st.get("injected") or []),
        "warnings": [e for e in errors if str(e).startswith(("starved run", "under floor"))],
        "finished_at": st.get("finished_at"),
    }


def _heartbeat_branch_state() -> dict:
    """Latest outcome per heartbeat branch (a week back; branches fire sparsely)."""
    latest: dict[str, dict] = {}
    for o in heartbeat_store.recent(168):
        branch = _BRANCH_OF.get(o["kind"])
        if branch and branch not in latest:
            latest[branch] = {"kind": o["kind"], "detail": (o.get("detail") or "")[:120],
                              "ts": float(o["ts"])}
    return latest


def _surface_stat(surface_id: str) -> str | None:
    """One live phrase per surface. None when the backing store can't answer."""
    if surface_id == "pulse_feed":
        from . import pulse_store
        from .scheduler import TZ
        today = datetime.now(TZ).date().isoformat()
        n = sum(1 for c in pulse_store.list_cards() if c.get("day") == today)
        return f"{n} card{'s' if n != 1 else ''} today"
    if surface_id == "veins":
        from . import pulse_store
        n = sum(1 for c in pulse_store.list_cards() if (c.get("kind") or "research") != "research")
        return f"{n} active card{'s' if n != 1 else ''}"
    if surface_id == "memory":
        from . import vera_memory_store
        n = len(vera_memory_store.core())
        return f"{n} core fact{'s' if n != 1 else ''}"
    if surface_id == "actions":
        n = action_store.pending_count()
        return f"{n} pending proposal{'s' if n != 1 else ''}"
    return None


@router.get("/agentic/graph", tags=["agentic"])
async def graph():
    from .scheduler import REGISTRY, running_jobs
    running = running_jobs()
    flows = []
    for job_id, (label, _cron, _handler) in REGISTRY.items():
        face = FLOW_FACE.get(job_id, _DEFAULT_FACE)
        flow = {
            "id": job_id,
            "label": face.get("label", label),
            "title": label,
            "kind": "heartbeat" if job_id == "heartbeat" else "job",
            "icon": face["icon"],
            "tint": face["tint"],
            "group": face["group"],
            "feeds": face["feeds"],
            "tools": face["tools"],
            "running": job_id in running,
        }
        if face.get("stages"):
            flow["stage_layout"] = face.get("stage_layout", "pipeline")
            flow["stages"] = face["stages"]
        if job_id == "pulse":
            try:
                flow["stage_state"] = _pulse_stage_state()
                if (flow["stage_state"] or {}).get("state") == "running":
                    flow["running"] = True
            except Exception as e:  # noqa: BLE001 — state is garnish, topology must survive
                log.warning("graph: pulse stage state failed: %s", e)
                flow["stage_state"] = None
        if job_id == "heartbeat":
            try:
                flow["branch_state"] = _heartbeat_branch_state()
            except Exception as e:  # noqa: BLE001
                log.warning("graph: heartbeat branch state failed: %s", e)
                flow["branch_state"] = {}
        flows.append(flow)
    surfaces = []
    for s in SURFACES:
        stat = None
        try:
            stat = _surface_stat(s["id"])
        except Exception as e:  # noqa: BLE001
            log.warning("graph: surface stat %s failed: %s", s["id"], e)
        surfaces.append({**s, "stat": stat})
    # Explicit edge list (flow -> surface), derived from the same feeds the flows declare:
    # one source of truth, two readings. The future editor mutates edges through this shape.
    edges = [{"from": f["id"], "to": sid} for f in flows for sid in f["feeds"]]
    return {"flows": flows, "surfaces": surfaces, "edges": edges}


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
