"""Vera memory router — read/write/core/groom over her world-model store.

Writes are FREE (her own knowledge, no external effect). The `/memory/self/core` digest is what the
OWUI inlet filter injects on every request; `/memory/self/recall` backs the on-demand recall tool.
Routes live under /memory/self/* — distinct from the existing OWUI-memory groomer at /memory/groom.
"""
import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from . import groom_common as gcm
from . import pulse_store as ps
from . import vera_interests_store as vi
from . import vera_memory_store as vm

router = APIRouter()
AGENT_TOKEN = os.environ.get("KNOWLEDGE_AGENT_TOKEN", "")  # reuse the coding-agent token for groom


class WriteBody(BaseModel):
    topic: str
    content: str
    source: str = "vera"
    confidence: float = 0.6
    tier: str = "archive"           # archive (default) | core | scratch (ephemeral scribble pad)
    ttl_hours: int | None = None    # scratch only; defaults to the store's SCRATCH_TTL_HOURS
    provenance: dict | None = None
    kind: str = "fact"              # fact (grounded) | opinion (her take; must cite facts)
    fact_refs: list[str] | None = None  # opinion only: ids of the facts it is anchored to


@router.post("/memory/self/write", tags=["vera_memory"])
async def self_write(b: WriteBody):
    if b.tier not in vm.TIERS:
        raise HTTPException(400, f"tier must be one of {vm.TIERS}")
    if b.kind not in ("fact", "opinion"):
        raise HTTPException(400, "kind must be fact|opinion")
    try:
        eid = vm.write(b.topic, b.content, source=b.source, confidence=b.confidence,
                       tier=b.tier, ttl_hours=b.ttl_hours, provenance=b.provenance,
                       kind=b.kind, fact_refs=b.fact_refs)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": eid}


@router.post("/memory/self/mirror", tags=["vera_memory"])
async def self_mirror():
    """Write + git-commit the legible MEMORY.md view (the store stays source of truth)."""
    return {"ok": vm.mirror_markdown()}


@router.get("/memory/self/recall", tags=["vera_memory"])
async def self_recall(q: str | None = None, limit: int = 8, kind: str | None = None):
    return {"results": vm.recall(query=q, limit=limit, kind=kind)}


@router.get("/memory/self/core", tags=["vera_memory"])
async def self_core():
    c = vm.core()
    return {"digest": vm.core_digest(), "count": len(c)}


@router.get("/memory/self/interests", tags=["vera_memory"])
async def self_interests(active: bool = False):
    """Her own emergent interests. active=true returns only those off cooldown (what a tick
    would actually choose from)."""
    items = vi.active(limit=50) if active else vi.all_interests()
    return {"interests": items, "count": len(items)}


class TierBody(BaseModel):
    id: str
    tier: str


@router.post("/memory/self/set_tier", tags=["vera_memory"])
async def self_set_tier(b: TierBody):
    if b.tier not in ("core", "archive"):
        raise HTTPException(400, "tier must be core|archive")
    vm.set_tier(b.id, b.tier)
    return {"ok": True}


class GroomBody(BaseModel):
    core_max: int = 50
    expire_archive_days: int = 0


@router.post("/memory/self/groom", tags=["vera_memory"])
async def self_groom(b: GroomBody, x_agent_token: str = Header(default="")):
    if not AGENT_TOKEN or x_agent_token != AGENT_TOKEN:
        raise HTTPException(403, "groom requires X-Agent-Token")
    return vm.groom(core_max=b.core_max, expire_archive_days=b.expire_archive_days)


class RestoreBody(BaseModel):
    card_id: str
    op_index: int


def _load_memory_op(b: RestoreBody):
    card = ps.get_card(b.card_id)
    if not card:
        raise HTTPException(404, "card not found")
    ops = card.get("change_set") or []
    if b.op_index < 0 or b.op_index >= len(ops):
        raise HTTPException(404, "no such op")
    op = ops[b.op_index]
    if op.get("store", "memory") != "memory":
        raise HTTPException(400, "not a memory-store op — use /knowledge/restore")
    return op


def _undo_memory(op, card_id):
    """Reverse one memory op. Idempotent — vm.write is keyed on (topic, content); delete is a no-op
    if already gone."""
    t = op.get("type")
    before = op.get("before") or []
    after = op.get("after")
    if t in ("forget", "merge"):
        for bel in before:  # bring the original belief(s) back from the snapshot
            vm.write(bel["topic"], bel["content"], source="restore",
                     confidence=bel.get("confidence") or 0.6, tier=bel.get("tier") or "archive",
                     provenance={"restored_from": card_id})
        if t == "merge" and after and after.get("id"):
            vm.delete(after["id"])  # drop the combined belief — "undo merge"
    elif t in ("promote", "archive") and before:
        vm.set_tier(before[0]["id"], before[0].get("tier") or "archive")  # flip the tier back
    else:
        raise HTTPException(400, f"cannot restore op type '{t}'")
    vm.mirror_markdown()
    return {"restored": t, "core_count": len(vm.core())}


@router.post("/memory/restore", tags=["vera_memory"])
async def restore(b: RestoreBody):
    """Reverse one op from a memory-tending card's change-set. User-initiated undo of
    Vera's own autonomous (free) memory edits, so it's not token-gated. Stale-guarded — won't clobber
    a belief a later run already re-edited."""
    op = _load_memory_op(b)
    if gcm.stale_snapshot(op):
        return {"ok": False, "stale": True,
                "note": "this was changed again since — review before restoring"}
    return {"ok": True, **_undo_memory(op, b.card_id)}


@router.post("/memory/reject", tags=["vera_memory"])
async def reject(b: RestoreBody):
    """Reject = undo + don't-redo — reverse the op AND suppress it so the next groom run
    won't repeat it (keyed on the belief's stable topic+content identity)."""
    op = _load_memory_op(b)
    if gcm.stale_snapshot(op):
        return {"ok": False, "stale": True,
                "note": "this was changed again since — review before rejecting"}
    res = _undo_memory(op, b.card_id)
    key = gcm.suppress("memory", op["type"], gcm.op_identity(op),
                       reason=op.get("reason") or "rejected by the owner")
    return {"ok": True, "rejected": True, "suppression": key, **res}
