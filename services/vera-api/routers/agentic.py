"""Agentic activity feed and canvas graph — what Vera does on her own, as data.

The graph is the canvas manifest: every flow, the
surface each one feeds, and per-flow presentation/topology metadata. Declarative in
the vein-catalog spirit — the app renders whatever this says; a new capability is a
new entry here, never an app release. This is also the reserved editor lane: a
server-declared graph can later accept mutations.
"""
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from . import action_store, scheduler_store
from . import workflow_registry
from . import workflow_store

log = logging.getLogger("agentic")
router = APIRouter()

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

# Per-flow canvas face: presentation label (the registry label stays the formal name),
# icon/tint (SF Symbol + the app's chart-palette tint names), thematic group, the
# surfaces the flow feeds, and the tools it is known to use (static attribution; the
# activity feed adds per-event attribution on top). Flows with `stages` drill in.
FLOW_FACE: dict[str, dict] = {
    "pulse":          {"label": "Pulse briefing", "icon": "newspaper", "tint": "accent",
                       "group": "Ambient", "feeds": ["pulse_feed"],
                       "tools": ["websearch", "vera-image"],
                       "stage_layout": "pipeline", "stages": _PULSE_STAGES},
    "home_model":     {"label": "Home model", "icon": "house", "tint": "cyan",
                       "group": "Home", "feeds": ["actions"], "tools": []},
    "home_reconcile": {"label": "Map reconcile", "icon": "checklist", "tint": "cyan",
                       "group": "Home", "feeds": ["veins"], "tools": []},
    "home_digest":    {"label": "Rhythm digest", "icon": "doc.text", "tint": "cyan",
                       "group": "Home", "feeds": ["veins"], "tools": []},
    "conversation_extract": {"label": "Conversation extraction", "icon": "text.bubble",
                             "tint": "purple", "group": "Memory", "feeds": ["memory"], "tools": []},
    "weight_fit":     {"label": "Weight fit", "icon": "chart.xyaxis.line", "tint": "purple",
                       "group": "Memory", "feeds": ["memory"], "tools": []},
}

# A job with no authored face still renders (and the test suite flags the omission).
_DEFAULT_FACE = {"icon": "clock", "tint": "gray", "group": "Other", "feeds": [], "tools": []}


class WorkflowDefinitionUpdate(BaseModel):
    definition: dict


def _vein_face(job_id: str) -> dict:
    from . import pulse_veins
    spec = pulse_veins.manifest(job_id.removeprefix("vein_")) or {}
    return {"label": spec.get("label", job_id), "icon": spec.get("icon", "clock"),
            "tint": "cyan", "group": "Ambient", "feeds": ["veins"], "tools": []}


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
        from . import profile_graph_store as pg
        n = len(pg.all_nodes())
        return f"{n} graph node{'s' if n != 1 else ''}"
    if surface_id == "actions":
        n = action_store.pending_count()
        return f"{n} pending proposal{'s' if n != 1 else ''}"
    return None


@router.get("/agentic/graph", tags=["agentic"])
async def graph():
    from .scheduler import _registry, running_jobs
    running = running_jobs()
    flows = []
    for job_id, (label, _cron, _handler) in _registry().items():
        face = FLOW_FACE.get(job_id)
        if face is None:
            face = _vein_face(job_id) if job_id.startswith("vein_") else _DEFAULT_FACE
        flow = {
            "id": job_id,
            "label": face.get("label", label),
            "title": label,
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
                active = workflow_store.active("pulse")
                flow["stage_layout"] = "pipeline"
                flow["stages"] = active["definition"].get("nodes") or []
                flow["workflow"] = {"version": active["version"], "state": active["state"], "editable": True}
                flow["stage_state"] = _pulse_stage_state()
                if (flow["stage_state"] or {}).get("state") == "running":
                    flow["running"] = True
            except Exception as e:  # noqa: BLE001 — state is garnish, topology must survive
                log.warning("graph: pulse stage state failed: %s", e)
                flow["stage_state"] = None
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


@router.get("/agentic/workflows/{workflow_id}", tags=["agentic"])
async def workflow(workflow_id: str):
    try:
        active = workflow_store.active(workflow_id)
    except KeyError:
        raise HTTPException(404, "workflow not found")
    return {"workflow": active, "latest_run": workflow_store.latest_run(workflow_id)}


@router.get("/agentic/workflows/{workflow_id}/catalog", tags=["agentic"])
async def workflow_catalog(workflow_id: str):
    try:
        workflow_store.active(workflow_id)
    except KeyError:
        raise HTTPException(404, "workflow not found")
    return {"nodes": workflow_registry.catalog(), "profile": workflow_registry.profile_for(workflow_id)}


@router.post("/agentic/workflows/{workflow_id}/drafts", tags=["agentic"])
async def create_workflow_draft(workflow_id: str):
    try:
        return {"workflow": workflow_store.create_draft(workflow_id)}
    except KeyError:
        raise HTTPException(404, "workflow not found")


@router.put("/agentic/workflow-drafts/{version_id}", tags=["agentic"])
async def update_workflow_draft(version_id: str, req: WorkflowDefinitionUpdate):
    try:
        return {"workflow": workflow_store.save_draft(version_id, req.definition)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/agentic/workflow-drafts/{version_id}/promote", tags=["agentic"])
async def promote_workflow_draft(version_id: str):
    try:
        return {"workflow": workflow_store.promote(version_id)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/agentic/activity", tags=["agentic"])
async def activity(hours: int = Query(24, ge=1, le=168)):
    events: list[dict] = []
    for name, collect in (("action", _action_events),
                          ("scheduler", _scheduler_events)):
        try:
            events.extend(collect(hours))
        except Exception as e:  # noqa: BLE001 — one bad source must never empty the feed
            log.warning("activity source %s failed: %s", name, e)
    events.sort(key=lambda e: e["ts"], reverse=True)
    return {"hours": hours, "events": events}
