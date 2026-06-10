"""Knowledge router — read / propose / commit / promote over the Home Knowledge store.

The only write path is propose -> commit (preview-gated, idempotent). promote codifies a type's
schema and is restricted to the coding agent via the X-Agent-Token header.
"""
import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from . import knowledge_store as ks

router = APIRouter()
AGENT_TOKEN = os.environ.get("KNOWLEDGE_AGENT_TOKEN", "")


@router.get("/knowledge/query", tags=["knowledge"])
async def kq(type: str | None = None, q: str | None = None, limit: int = 50):
    return {"results": ks.query(type=type, q=q, limit=limit)}


@router.get("/knowledge/entity/{entity_id}", tags=["knowledge"])
async def kentity(entity_id: str):
    e = ks.get(entity_id)
    if not e:
        raise HTTPException(404, "not found")
    return e


@router.get("/knowledge/types", tags=["knowledge"])
async def ktypes():
    return {"types": ks.types()}


class Propose(BaseModel):
    op: str = "set"
    entity_id: str | None = None
    type: str | None = None
    name: str | None = None
    attrs: dict | None = None
    source: str = "chat"
    actor: str = "vera"
    chat_id: str | None = None
    message_id: str | None = None


@router.post("/knowledge/propose", tags=["knowledge"])
async def kpropose(p: Propose):
    if p.op not in ("set", "delete"):
        raise HTTPException(400, "op must be set|delete")
    return ks.propose(**p.model_dump())


class Commit(BaseModel):
    token: str


@router.post("/knowledge/commit", tags=["knowledge"])
async def kcommit(c: Commit):
    return ks.commit(c.token)


class Promote(BaseModel):
    type: str
    json_schema: dict
    by: str = "coding-agent"


@router.post("/knowledge/promote", tags=["knowledge"])
async def kpromote(p: Promote, x_agent_token: str = Header(default="")):
    if not AGENT_TOKEN or x_agent_token != AGENT_TOKEN:
        raise HTTPException(403, "promote requires X-Agent-Token")
    return ks.promote(p.type, p.json_schema, by=p.by)
