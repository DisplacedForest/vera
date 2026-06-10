"""Health checks — liveness for the load-bearing services.

  GET  /health/services -> {ok, services:[{name,ok,detail}]}
  POST /health/check     -> run checks; if anything is DOWN, inject a Pulse card alert
                            (zero-floor: all healthy = no card).

Cron POST /health/check every ~15 min so a dead service surfaces proactively, instead of
being discovered when you go to use Vera.
"""
import asyncio
import os

import aiohttp
from fastapi import APIRouter
from pydantic import BaseModel

from . import env_compat
from . import pulse_store as store
from .pulse import _inject

router = APIRouter()


def _services() -> list[dict]:
    """Health targets derived from the SAME env vars that wire each integration —
    only what the deployment actually declares gets checked, nothing is assumed."""
    from urllib.parse import urlparse

    svcs: list[dict] = [{"name": "vera-api", "self": True}]
    owui = os.environ.get("OWUI_BASE", "").rstrip("/")
    if owui:
        svcs.append({"name": "open-webui", "url": f"{owui}/health"})
    vera = os.environ.get("VERA_BASE", "").rstrip("/")
    if vera:
        svcs.append({"name": "llm-server", "url": f"{vera}/models"})
    searx = env_compat.read("SEARXNG_BASE")
    if searx:
        root = searx.split("/search")[0].rstrip("/")
        svcs.append({"name": "searxng", "url": f"{root}/", "ok": [200, 302]})
    pw = os.environ.get("PLAYWRIGHT_WS", "")
    if pw:
        u = urlparse(pw)
        if u.hostname and u.port:
            svcs.append({"name": "playwright", "tcp": (u.hostname, u.port)})
    # Extra TCP liveness watches, e.g. a flaky scanner Pi: "name=host:port,name2=host2:port2".
    for entry in os.environ.get("HEALTH_TCP_WATCHES", "").split(","):
        entry = entry.strip()
        if not entry or "=" not in entry or ":" not in entry:
            continue
        name, hostport = entry.split("=", 1)
        host, _, port = hostport.rpartition(":")
        try:
            svcs.append({"name": name.strip(), "tcp": (host.strip(), int(port))})
        except ValueError:
            continue
    return svcs


SERVICES = _services()


async def _check(session, svc):
    if svc.get("self"):
        return {"name": svc["name"], "ok": True, "detail": "responding"}
    if svc.get("tcp"):
        host, port = svc["tcp"]
        # Retry: the container's first contact with a LAN host can fail with a cold ARP/neighbor
        # miss ("No route to host") even when the host is up; a single blip shouldn't read as DOWN.
        last = ""
        for attempt in range(3):
            try:
                _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5)
                writer.close()
                return {"name": svc["name"], "ok": True, "detail": f"tcp {port} open"}
            except Exception as e:
                last = str(e)[:60]
                if attempt < 2:
                    await asyncio.sleep(1)
        return {"name": svc["name"], "ok": False, "detail": f"tcp closed: {last}"}
    try:
        async with session.get(svc["url"], timeout=aiohttp.ClientTimeout(total=8)) as r:
            ok = r.status in svc.get("ok", [200])
            return {"name": svc["name"], "ok": ok, "detail": f"HTTP {r.status}"}
    except Exception as e:
        return {"name": svc["name"], "ok": False, "detail": str(e)[:70]}


async def _run():
    async with aiohttp.ClientSession() as s:
        return await asyncio.gather(*[_check(s, svc) for svc in SERVICES])


@router.get("/health/services", tags=["health"])
async def services():
    res = await _run()
    return {"ok": all(r["ok"] for r in res), "services": res}


class HealthCheck(BaseModel):
    pulse_folder_id: str | None = None  # ignored (kept for cron compat)


def _active_health_cards() -> list[dict]:
    """The currently-active health producer cards (kind=status, category=health)."""
    return [c for c in store.list_cards()
            if c.get("kind") == "status" and c.get("category") == "health"]


@router.post("/health/check", tags=["health"])
async def check(req: HealthCheck):
    """Fold service health into the System lane. A failed/degraded check injects a
    kind=status, category=health card (lands under the System chip's Health group); when the
    down-set changes or everything recovers, stale cards are cleared so the chip reflects live
    state on the next pass (the daily sweep is just the overnight backstop)."""
    from . import pulse_lanes
    if not pulse_lanes.is_enabled("status"):
        return {"ok": False, "disabled": True, "detail": pulse_lanes.gate_reason("status")}
    if pulse_lanes.option_values("status").get("src_service_health") is False:
        return {"ok": True, "disabled": True, "detail": "service-health probes are off in the System lane"}
    res = await _run()
    down = [r for r in res if not r["ok"]]
    title = ", ".join(d["name"] for d in down) + " down" if down else ""
    out = {"ok": True, "down": [d["name"] for d in down], "alerted": False, "cleared": 0, "services": res}

    # Clear any health card whose down-set no longer matches current (a service recovered, or all
    # green) — keeps the System chip honest. A still-matching card is left in place (dedup: a
    # persistently-down service alerts ONCE, not every 15-min pass).
    existing = _active_health_cards()
    for c in existing:
        if c["title"] != title:
            store.delete_card(c["id"])
            out["cleared"] += 1

    if down and not any(c["title"] == title for c in existing):  # zero-floor + dedup
        body = "**Service health alert**\n\n" + "\n".join(f"- `{d['name']}` is DOWN — {d['detail']}" for d in down)
        await _inject(title, body, summary="A service Vera depends on is down.",
                      kind="status", category="health", severity="alert")
        out["alerted"] = True
    return out
