"""Health checks — liveness for the load-bearing services.

  GET /health/services -> {ok, services:[{name,ok,detail}]}

The `service_health` vein block runs the same probe set for the System vein: one
standing situation per down service, retired by the engine when it recovers.
"""
import asyncio
import os

import aiohttp
from fastapi import APIRouter

from . import env_compat
from . import vein_engine

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


async def _block_service_health(items, params, ctx):
    if (ctx.get("options") or {}).get("src_service_health") is False:
        return items
    res = await _run()
    return items + [{"key": f"health:{r['name']}", "title": f"{r['name']} down",
                     "content": f"the `{r['name']}` service is not responding",
                     "severity": "alert", "category": "health"}
                    for r in res if not r["ok"]]


vein_engine.register("service_health", _block_service_health, monitor=True)
