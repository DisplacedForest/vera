"""Built-in scheduler — vera-api runs its own recurring jobs; no n8n, no host cron.

Every job that is a pure internal call (the same handler its HTTP endpoint uses) runs
from one asyncio loop here. Host-level work that genuinely needs the host (filesystem
backups, bringing up a local model server) stays outside — everything else is this.

  GET  /scheduler/jobs           -> the job table (cron, enabled, last/next run)
  PUT  /scheduler/jobs/{id}      -> change cron and/or enabled (persisted)
  POST /scheduler/jobs/{id}/run  -> fire a job now (manual)

Config precedence per job: env (SCHEDULE_<ID>, SCHEDULE_<ID>_ENABLED — headless installs)
beats the db row (runtime edits from the Agentic tab) beats the registry default. A job
whose env says disabled is LOCKED: the API refuses to enable it (double-fire guard while
an external scheduler still owns it). SCHEDULER_ENABLED=false kills the loop entirely.

Cron expressions are evaluated in HOME_TZ. Failures never stop the loop; each job records
its last outcome for the UI. A job still running when its next fire comes due is skipped
(no overlapping instances).
"""

import asyncio
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import scheduler_store as store

router = APIRouter()
log = logging.getLogger("vera.scheduler")

ENABLED = os.environ.get("SCHEDULER_ENABLED", "true").strip().lower() != "false"
TZ = ZoneInfo(os.environ.get("HOME_TZ", "UTC"))
_POLL_SECONDS = 20


# ---- job handlers (each calls the same code its endpoint runs) ----------------------

async def _job_pulse():
    from . import pulse
    # Follow the 202 trigger to completion so the job's recorded outcome is the run's
    # actual result (cards, starvation warnings, gate kills), not the acknowledgement.
    return await pulse.run_outcome(await pulse.run(pulse.PulseRequest()))


async def _job_weather():
    from . import weather
    return await weather.check(weather.WeatherRequest())


async def _job_signals():
    from . import signals
    return await signals.check(signals.SignalsRequest())


async def _job_memory_groom():
    from . import memory
    return await memory.groom()


async def _job_home_model():
    from . import home_model
    return await home_model.refresh()


async def _job_home_reconcile():
    from . import home_reconcile
    return await home_reconcile.run()


async def _job_home_digest():
    from . import home
    return await home.run_digest()


async def _job_heartbeat():
    from . import heartbeat
    return await heartbeat.tick(heartbeat.TickRequest())


async def _job_healthcheck():
    from . import health
    return await health.check(health.HealthCheck())


async def _job_updates():
    from . import updates
    return await updates.check(updates.UpdateCheck())


async def _job_media_curate():
    from . import media_curation
    return await media_curation.curate()


# id -> (label, default cron, handler). The shipped schedule; everything overridable.
REGISTRY: dict[str, tuple[str, str, object]] = {
    "pulse":          ("Pulse briefing run",        "0 5 * * *",    _job_pulse),
    "weather":        ("Weather check",             "0 */6 * * *",  _job_weather),
    "signals":        ("Signals check",             "0 6,18 * * *", _job_signals),
    "memory_groom":   ("Episodic memory groom",     "0 4 * * *",    _job_memory_groom),
    "home_model":     ("Home model refresh",        "30 3 * * *",   _job_home_model),
    "home_reconcile": ("Home map reconcile",        "0 3 * * *",    _job_home_reconcile),
    "home_digest":    ("Home rhythm digest",        "0 2 * * *",    _job_home_digest),
    "heartbeat":      ("Heartbeat tick",            "*/20 * * * *", _job_heartbeat),
    "healthcheck":    ("Service health probe",      "*/15 * * * *", _job_healthcheck),
    "updates":        ("Stack updates check",       "30 7 * * *",   _job_updates),
    "media_curate":   ("Media curation digest",     "0 9 * * 0",    _job_media_curate),
}


# Jobs tied to an integration's experimental feature inherit its gate: while the gate
# is closed the job shows as disabled (with the reason), the loop never fires it, and
# manual runs are refused. Toggling the feature opens/closes these immediately.
def _gate_home_modeling() -> str | None:
    from . import integrations
    if integrations.feature_enabled("home_assistant", "home_modeling"):
        return None
    return "requires the home_assistant integration with its home_modeling feature enabled"


def _gate_media_curation() -> str | None:
    from . import integrations
    if integrations.feature_enabled("overseerr", "media_curation"):
        return None
    return "requires the overseerr integration with its media_curation feature enabled"


def _vein_gate(kind: str):
    """Gate factory: a vein's producer jobs run only while the vein is enabled (and its
    requirements hold) — no orphaned producers burning tokens for a hidden chip."""
    def gate() -> str | None:
        from . import pulse_veins
        return pulse_veins.gate_reason(kind)
    return gate


def _gate_media() -> str | None:
    return _vein_gate("media")() or _gate_media_curation()


GATES: dict[str, object] = {
    "home_model": _gate_home_modeling,
    "home_reconcile": _gate_home_modeling,
    "home_digest": _gate_home_modeling,
    "media_curate": _gate_media,
    "weather": _vein_gate("weather"),
    "signals": _vein_gate("signals"),
    "updates": _vein_gate("status"),
    "healthcheck": _vein_gate("status"),
}


# ---- effective config (env > db > registry) ------------------------------------------

def _env_cron(job_id: str) -> str | None:
    v = os.environ.get(f"SCHEDULE_{job_id.upper()}", "").strip()
    return v or None


def _env_enabled(job_id: str) -> bool | None:
    v = os.environ.get(f"SCHEDULE_{job_id.upper()}_ENABLED", "").strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    return None


def _gate_reason(job_id: str) -> str | None:
    gate = GATES.get(job_id)
    return gate() if gate else None


def _effective(job_id: str, row: dict | None) -> dict:
    label, default_cron, _ = REGISTRY[job_id]
    env_cron, env_enabled = _env_cron(job_id), _env_enabled(job_id)
    cron = env_cron or (row or {}).get("cron") or default_cron
    if env_enabled is not None:
        enabled = env_enabled
    elif row is not None and row.get("enabled") is not None:
        enabled = bool(row["enabled"])
    else:
        enabled = True
    gated = _gate_reason(job_id)
    if gated:
        enabled = False
    return {"id": job_id, "label": label, "cron": cron, "enabled": enabled,
            "env_locked": env_enabled is not None, "gated": gated,
            "last_run": ({"ts": row["last_ts"], "ok": bool(row["last_ok"]),
                          "detail": row["last_detail"]}
                         if row and row.get("last_ts") else None)}


def _next_fire(cron: str, after: datetime) -> datetime:
    return croniter(cron, after).get_next(datetime)


def jobs_view() -> list[dict]:
    rows = store.overrides()
    out = []
    now = datetime.now(TZ)
    for job_id in REGISTRY:
        j = _effective(job_id, rows.get(job_id))
        try:
            j["next_run"] = _next_fire(j["cron"], now).isoformat() if j["enabled"] and ENABLED else None
        except (ValueError, KeyError):
            j["next_run"] = None
        out.append(j)
    return out


# ---- the loop -------------------------------------------------------------------------

_task: asyncio.Task | None = None
_running: set[str] = set()


async def _fire(job_id: str, manual: bool = False):
    if job_id in _running:
        store.record_outcome(job_id, False, "skipped: previous run still in progress")
        return
    _running.add(job_id)
    try:
        gated = _gate_reason(job_id)  # re-check at fire time — the gate may have closed
        if gated:
            log.info("job %s skipped: %s", job_id, gated)
            return
        handler = REGISTRY[job_id][2]
        result = await handler()
        store.record_outcome(job_id, True, str(result)[:500])
        log.info("job %s ok%s", job_id, " (manual)" if manual else "")
    except Exception as e:  # noqa: BLE001 — one bad job must never kill the loop
        store.record_outcome(job_id, False, f"{type(e).__name__}: {e}")
        log.warning("job %s failed: %s", job_id, e)
    finally:
        _running.discard(job_id)


async def _loop():
    # next-fire computed per job and refreshed after each fire or config change; cheap
    # enough to recompute every poll, which also picks up runtime cron edits immediately.
    last_check = datetime.now(TZ)
    while True:
        await asyncio.sleep(_POLL_SECONDS)
        now = datetime.now(TZ)
        rows = store.overrides()
        for job_id in REGISTRY:
            j = _effective(job_id, rows.get(job_id))
            if not j["enabled"]:
                continue
            try:
                due = _next_fire(j["cron"], last_check)
            except (ValueError, KeyError):
                continue  # bad cron edit — job reports next_run=None in the API
            if due <= now:
                asyncio.create_task(_fire(job_id))
        last_check = now


async def start():
    global _task
    # Settle the vein store's one-time seeding decision now, before any job runs —
    # jobs write data artifacts that would otherwise read as a prior deployment.
    from . import vein_store
    vein_store.load()
    if ENABLED and _task is None:
        _task = asyncio.create_task(_loop())
        log.info("scheduler running (%d jobs, poll %ds, tz %s)", len(REGISTRY), _POLL_SECONDS, TZ)
    elif not ENABLED:
        log.info("scheduler disabled (SCHEDULER_ENABLED=false)")


async def stop():
    global _task
    if _task is not None:
        _task.cancel()
        _task = None


# ---- API -------------------------------------------------------------------------------

class JobUpdate(BaseModel):
    cron: str | None = None
    enabled: bool | None = None


@router.get("/scheduler/jobs", tags=["scheduler"])
async def list_jobs():
    return {"scheduler_enabled": ENABLED, "jobs": jobs_view()}


@router.put("/scheduler/jobs/{job_id}", tags=["scheduler"])
async def update_job(job_id: str, req: JobUpdate):
    if job_id not in REGISTRY:
        raise HTTPException(status_code=404, detail=f"unknown job '{job_id}'")
    if req.cron is not None:
        if not croniter.is_valid(req.cron):
            raise HTTPException(status_code=422, detail=f"invalid cron expression '{req.cron}'")
        if _env_cron(job_id):
            raise HTTPException(status_code=409, detail="schedule is pinned by env (SCHEDULE_*). Change it there")
    if req.enabled is not None and _env_enabled(job_id) is not None:
        raise HTTPException(status_code=409, detail="enabled is pinned by env (SCHEDULE_*_ENABLED). Change it there")
    store.set_override(job_id, cron=req.cron, enabled=req.enabled)
    return _effective(job_id, store.overrides().get(job_id)) | {
        "next_run": None if not ENABLED else _next_fire(
            _effective(job_id, store.overrides().get(job_id))["cron"], datetime.now(TZ)).isoformat()}


@router.post("/scheduler/jobs/{job_id}/run", tags=["scheduler"])
async def run_job(job_id: str):
    if job_id not in REGISTRY:
        raise HTTPException(status_code=404, detail=f"unknown job '{job_id}'")
    gated = _gate_reason(job_id)
    if gated:
        raise HTTPException(status_code=409, detail=gated)
    asyncio.create_task(_fire(job_id, manual=True))
    return {"ok": True, "fired": job_id}
