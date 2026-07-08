"""Whole-house event capture — a live HA WebSocket subscriber that records EVERY state
change + automation/script fire into home_events_store, the substrate the home-rhythm model
learns from.

Gated behind the home_assistant integration's experimental `home_modeling` feature: a
supervisor task (started by main.py's lifespan) watches the registry and starts capture
only while the feature is enabled and acknowledged — never at boot by default. Enabling
or disabling through the integrations API takes effect within the supervisor's poll
interval, no restart. Reconnects on drop, never blocks the app, purges past the
retention window daily. Single-worker assumed (one subscriber).

Env: HOME_EVENTS_IGNORE="sensor.foo,sensor.bar" drops matching entity_ids (substring
match) — default captures everything; any drop is explicit, never silent.
"""
import asyncio
import json
import os
import time
from datetime import datetime

import aiohttp
from fastapi import APIRouter

from . import home_events_store as store
from . import integrations, series_store

router = APIRouter()

# state_changed = every entity transition; the other two log automation/script fires explicitly.
# call_service is deliberately omitted (high-noise; causation already rides on state_changed.context).
EVENT_TYPES = ["state_changed", "automation_triggered", "script_started"]
_IGNORE = [s.strip() for s in os.environ.get("HOME_EVENTS_IGNORE", "").split(",") if s.strip()]
_SUPERVISE_SECONDS = 30

_state = {"connected": False, "events": 0, "last_event_ts": None, "last_error": None,
          "started_at": None, "ignored_globs": _IGNORE}
_task: asyncio.Task | None = None
_supervisor: asyncio.Task | None = None


def _gate_open() -> bool:
    return integrations.feature_enabled("home_assistant", "home_modeling")


def _ha() -> tuple[str, str]:
    """(url, token) from the registry at call time — empty when the integration is off."""
    cfg = integrations.integration("home_assistant") or {}
    return cfg.get("url", ""), cfg.get("token", "")


def _ws_url(url: str) -> str:
    return url.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"


def _parse_iso(s: str) -> int:
    try:
        return int(datetime.fromisoformat((s or "").replace("Z", "+00:00")).timestamp())
    except Exception:
        return int(time.time())


def _ignored(entity_id: str) -> bool:
    return bool(_IGNORE) and bool(entity_id) and any(tok in entity_id for tok in _IGNORE)


def _record(msg: dict):
    ev = msg.get("event") or {}
    etype = ev.get("event_type")
    data = ev.get("data") or {}
    ts = _parse_iso(ev.get("time_fired") or "")
    ctx = ev.get("context")
    if etype == "state_changed":
        eid = data.get("entity_id") or ""
        if _ignored(eid):
            return
        old = data.get("old_state") or {}
        new = data.get("new_state") or {}
        store.insert({
            "ts": ts, "event_type": etype, "entity_id": eid, "domain": eid.split(".")[0],
            "old_state": old.get("state"), "new_state": new.get("state"),
            "attrs": new.get("attributes"), "context": ctx,
        })
        if eid.split(".")[0] == "sensor":
            from .home_model_mine import _as_float
            v = _as_float(new.get("state"))
            if v is not None:
                series_store.insert(eid, ts, v)
    else:  # automation_triggered / script_started
        eid = data.get("entity_id") or ""
        store.insert({
            "ts": ts, "event_type": etype, "entity_id": eid,
            "domain": eid.split(".")[0] if eid else etype.split("_")[0],
            "old_state": None, "new_state": data.get("name") or data.get("source"),
            "attrs": data, "context": ctx,
        })
    _state["events"] += 1
    _state["last_event_ts"] = ts


def _on_text(data: str, ctl: dict):
    msg = json.loads(data)
    if msg.get("type") == "event":
        try:
            _record(msg)
        except Exception as e:
            _state["last_error"] = f"record: {e}"
    now = time.time()
    if now - ctl["last_purge"] > 86400:  # daily retention sweep, inline
        try:
            store.purge()
            series_store.purge()
        except Exception:
            pass
        ctl["last_purge"] = now


async def _session(url: str, token: str, ctl: dict) -> bool:
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(_ws_url(url), heartbeat=30, timeout=aiohttp.ClientTimeout(total=30)) as ws:
            await ws.receive_json()  # {"type":"auth_required"}
            await ws.send_json({"type": "auth", "access_token": token})
            ack = await ws.receive_json()
            if ack.get("type") != "auth_ok":
                _state["last_error"] = f"auth failed: {ack.get('type')}"
                await asyncio.sleep(30)
                return False
            for i, et in enumerate(EVENT_TYPES, start=1):
                await ws.send_json({"id": i, "type": "subscribe_events", "event_type": et})
            _state["connected"] = True
            _state["last_error"] = None
            ctl["backoff"] = 1
            async for raw in ws:
                if raw.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
                if raw.type == aiohttp.WSMsgType.TEXT:
                    _on_text(raw.data, ctl)
    return True


async def _run():
    """Maintain the HA WebSocket subscription forever, reconnecting on any drop."""
    _state["started_at"] = int(time.time())
    ctl = {"backoff": 1, "last_purge": 0.0}
    while True:
        try:
            url, token = _ha()  # re-read per attempt so config edits apply on reconnect
            if not url or not token:
                _state["last_error"] = "home_assistant integration unconfigured"
                await asyncio.sleep(30)
                continue
            if not await _session(url, token, ctl):
                continue
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _state["last_error"] = str(e)
        _state["connected"] = False
        await asyncio.sleep(min(ctl["backoff"], 60))
        ctl["backoff"] = min(ctl["backoff"] * 2, 60)


async def _start_capture():
    global _task
    if _task is None:
        store.init()
        series_store.init()
        _task = asyncio.create_task(_run())


async def _stop_capture():
    global _task
    if _task:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass
        _task = None
        _state["connected"] = False


async def _supervise():
    """Hold capture in line with the registry: start it while the home_modeling gate is
    open, stop it when the gate closes. Runtime toggles apply within one poll."""
    while True:
        try:
            if _gate_open():
                await _start_capture()
            else:
                await _stop_capture()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — the supervisor must outlive any one error
            _state["last_error"] = f"supervise: {e}"
        await asyncio.sleep(_SUPERVISE_SECONDS)


async def start():
    """Launch the registry-watching supervisor (called from main.py lifespan)."""
    global _supervisor
    if _supervisor is None:
        _supervisor = asyncio.create_task(_supervise())


async def stop():
    global _supervisor
    if _supervisor:
        _supervisor.cancel()
        try:
            await _supervisor
        except (asyncio.CancelledError, Exception):
            pass
        _supervisor = None
    await _stop_capture()


@router.get("/home/events", tags=["home"])
async def events(limit: int = 100, entity_id: str | None = None,
                 event_type: str | None = None, since: int | None = None):
    """Inspect the captured event stream (also what the home model reads)."""
    return {"events": store.recent(limit=min(limit, 1000), entity_id=entity_id,
                                   event_type=event_type, since=since)}


@router.get("/home/events/stats", tags=["home"])
async def events_stats():
    """Capture health (is the subscriber connected, how many events) + store totals."""
    return {"enabled": _gate_open(), "capture": _state, "store": store.stats()}


@router.get("/home/series", tags=["home"])
async def series_index(min_points: int = 0):
    return {"entities": series_store.entities(min_points=min_points)}


@router.get("/home/series/{entity_id}", tags=["home"])
async def series_points(entity_id: str, since: int | None = None, until: int | None = None,
                        limit: int | None = None):
    return {"entity_id": entity_id,
            "points": series_store.series(entity_id, since=since, until=until,
                                          limit=min(limit, 100_000) if limit else None)}
