"""HTTP surface of the reminders bridge. Endpoints are sync defs (FastAPI runs
them in its threadpool) because the EventKit layer blocks on completion handlers."""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import eventkit_store as ek

app = FastAPI(title="vera-reminders")


def _require_access() -> None:
    if not ek.ensure_access():
        raise HTTPException(status_code=503, detail="Reminders access not granted on this Mac")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "reminders_access": ek.ensure_access()}


@app.get("/lists")
def lists() -> dict:
    _require_access()
    return {"ok": True, "lists": ek.lists()}


@app.get("/reminders")
def reminders(list: str | None = None, completed: bool = False) -> dict:
    _require_access()
    try:
        return {"ok": True, "reminders": ek.reminders(list, completed)}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"no reminders list named {e.args[0]!r}")


class CreateBody(BaseModel):
    list: str
    title: str
    notes: str | None = None
    due: str | None = None


@app.post("/reminders")
def create(req: CreateBody) -> dict:
    _require_access()
    try:
        return {"ok": True, "reminder": ek.create(req.list, req.title, req.notes, req.due)}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"no reminders list named {e.args[0]!r}")


class UpdateBody(BaseModel):
    completed: bool | None = None
    title: str | None = None
    notes: str | None = None
    due: str | None = None


@app.patch("/reminders/{rid}")
def update(rid: str, req: UpdateBody) -> dict:
    _require_access()
    try:
        return {"ok": True, "reminder": ek.update(rid, req.completed, req.title, req.notes, req.due)}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no reminder with id {rid!r}")
