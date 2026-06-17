"""Self-authoring write-path — Vera writes/refines her OWN docs: skills/protocols and
her HEARTBEAT.md, stored as native OWUI Skills. FREE (no gate) — her own knowledge, no external
effect. Every write is snapshotted to the revision log (authoring_store) for audit + rollback.

Guardrails: this path ONLY touches OWUI Skills (and the memory store, via the vera-memory
router's own endpoints) — never the model's core persona/system prompt, never another user's
data. It cannot actuate the world; actuation goes through the confirmation gate.
"""
import os
import re

import aiohttp
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import authoring_store as store

router = APIRouter()

OWUI_BASE = os.environ.get("OWUI_BASE", "").rstrip("/")
OWUI_KEY = os.environ.get("OWUI_KEY", "")
HEARTBEAT_SKILL_ID = "heartbeat"
_TIMEOUT = aiohttp.ClientTimeout(total=30)


def _require_owui():
    """The write path lives in OWUI skills; fail upfront with a clear 503, not mid-call."""
    if not OWUI_BASE or not OWUI_KEY:
        raise HTTPException(503, "authoring requires Open WebUI. Set OWUI_BASE and OWUI_KEY")


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-") or "skill"


def _hdr():
    return {"Authorization": f"Bearer {OWUI_KEY}", "Content-Type": "application/json"}


async def _skill_get(sid):
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{OWUI_BASE}/api/v1/skills/id/{sid}", headers=_hdr(), timeout=_TIMEOUT) as r:
            if r.status != 200:
                return None
            return await r.json()


async def _skill_upsert(sid, name, description, content):
    """Update the skill if it exists, else create it. Returns the skill id."""
    form = {"id": sid, "name": name, "description": description or "", "content": content}
    exists = await _skill_get(sid) is not None
    path = f"/api/v1/skills/id/{sid}/update" if exists else "/api/v1/skills/create"
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{OWUI_BASE}{path}", headers=_hdr(), json=form, timeout=_TIMEOUT) as r:
            if r.status != 200:
                raise HTTPException(502, f"OWUI skills {('update' if exists else 'create')} failed: "
                                        f"{r.status} {(await r.text())[:160]}")
    return sid


class SkillBody(BaseModel):
    name: str
    content: str
    id: str | None = None
    description: str | None = None


@router.post("/authoring/skill", tags=["authoring"])
async def author_skill(b: SkillBody):
    """Propose an OWUI skill write. A skill is durable, always-active cognition config, so this
    no longer writes directly: it stages a gated `owui.skill_upsert` proposal (visible in the
    Agentic activity feed) and the write applies only on confirmation. HEARTBEAT.md keeps its
    own sanctioned direct path (/authoring/heartbeat)."""
    _require_owui()
    sid = b.id or _slug(b.name)
    from . import actions
    ok, err = actions._stage("owui.skill_upsert",
                             {"name": b.name, "content": b.content, "id": sid,
                              "description": b.description},
                             source="self_author", actor="vera", chat_id=None, message_id=None)
    if err:
        raise HTTPException(400, err.get("error", "invalid skill proposal"))
    return {"proposed": True, "token": ok["token"], "preview": ok["preview"],
            "message": "proposed, awaiting confirmation"}


class HeartbeatBody(BaseModel):
    content: str


@router.post("/authoring/heartbeat", tags=["authoring"])
async def author_heartbeat(b: HeartbeatBody):
    """Vera rewrites her own HEARTBEAT.md (the standing proactive instructions read each heartbeat tick). Free; versioned."""
    _require_owui()
    store.snapshot(f"skill:{HEARTBEAT_SKILL_ID}", b.content, note="heartbeat")
    await _skill_upsert(HEARTBEAT_SKILL_ID, "Vera Heartbeat",
                        "Vera's standing proactive instructions (self-authored; read each heartbeat tick).",
                        b.content)
    return {"id": HEARTBEAT_SKILL_ID}


@router.get("/authoring/revisions", tags=["authoring"])
async def list_revisions(target: str):
    return {"revisions": store.revisions(target)}


class RevertBody(BaseModel):
    rev_id: int


@router.post("/authoring/revert", tags=["authoring"])
async def revert(b: RevertBody):
    """Roll a skill/heartbeat back to a prior version (re-applies that content, logged as a new version)."""
    _require_owui()
    r = store.get(b.rev_id)
    if not r:
        raise HTTPException(404, "revision not found")
    target = r["target"]
    if not target.startswith("skill:"):
        raise HTTPException(400, "can only revert skill targets")
    sid = target.split(":", 1)[1]
    cur = await _skill_get(sid)
    name = cur.get("name", sid) if cur else sid
    desc = cur.get("description", "") if cur else ""
    store.snapshot(target, r["content"], note=f"revert to #{b.rev_id}")
    await _skill_upsert(sid, name, desc, r["content"])
    return {"ok": True, "id": sid, "reverted_to": b.rev_id}
