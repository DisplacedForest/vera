"""Apple Reminders capability — proxy to the vera-reminders EventKit bridge.

The bridge (URL from the integration registry) runs on a Mac signed into the
household iCloud account; EventKit there sees shared lists, so Siri-added items
appear and writes sync back to every participant's devices."""
import aiohttp
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()


def _cfg() -> dict:
    from . import integrations
    cfg = integrations.integration("apple_reminders")
    if not cfg:
        raise HTTPException(status_code=503, detail=integrations.disabled_detail("apple_reminders"))
    return cfg


async def _call(method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict:
    cfg = _cfg()
    try:
        async with aiohttp.ClientSession() as s:
            async with s.request(method, f"{cfg['url']}{path}", params=params, json=body,
                                 timeout=aiohttp.ClientTimeout(total=20)) as r:
                data = await r.json(content_type=None)
                if r.status >= 400:
                    detail = (data or {}).get("detail", data) if isinstance(data, dict) else data
                    raise HTTPException(status_code=502,
                                        detail=f"reminders bridge {path} -> {r.status}: {detail}")
                return data
    except aiohttp.ClientError as e:
        raise HTTPException(status_code=502, detail=f"reminders bridge unreachable: {e}")


@router.get("/reminders/health", tags=["reminders"])
async def bridge_health():
    return await _call("GET", "/health")


@router.get("/reminders/lists", tags=["reminders"])
async def lists():
    return await _call("GET", "/lists")


@router.get("/reminders", tags=["reminders"])
async def reminders(list_name: str | None = Query(None, alias="list"), completed: bool = False):
    params: dict = {"completed": str(completed).lower()}
    if list_name:
        params["list"] = list_name
    return await _call("GET", "/reminders", params=params)


class CreateBody(BaseModel):
    list: str
    title: str
    notes: str | None = None
    due: str | None = None


@router.post("/reminders", tags=["reminders"])
async def create(req: CreateBody):
    return await _call("POST", "/reminders", body=req.model_dump(exclude_none=True))


class UpdateBody(BaseModel):
    completed: bool | None = None
    title: str | None = None
    notes: str | None = None
    due: str | None = None


@router.patch("/reminders/{rid}", tags=["reminders"])
async def update(rid: str, req: UpdateBody):
    return await _call("PATCH", f"/reminders/{rid}", body=req.model_dump(exclude_none=True))
