"""Knowledge-store Restore / Reject — the counterpart to /memory/restore for the OTHER store.
/memory/restore only knows beliefs (vm.write/delete/set_tier) — sending a knowledge op there
would 400 on a GC restore and write a merge/promote into the wrong store. This router reverses
knowledge ops through the gated knowledge API, so every undo is itself audited in the
revision log.

- POST /knowledge/restore  {card_id, op_index}  — undo one op (re-create a GC'd entity, un-merge,
  un-codify a promoted type). Idempotent. Stale-guarded (won't clobber state a later run changed).
- POST /knowledge/reject   {card_id, op_index}  — undo + record a durable suppression so the next
  groom run will not repeat this op.

User-initiated undo of Vera's own autonomous (free) edits, so it is not token-gated.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import groom_common as gcm
from . import knowledge_store as ks
from . import pulse_store as ps

router = APIRouter()


class RestoreBody(BaseModel):
    card_id: str
    op_index: int


def _load_op(b: RestoreBody):
    card = ps.get_card(b.card_id)
    if not card:
        raise HTTPException(404, "card not found")
    ops = card.get("change_set") or []
    if b.op_index < 0 or b.op_index >= len(ops):
        raise HTTPException(404, "no such op")
    op = ops[b.op_index]
    if op.get("store") != "knowledge":
        raise HTTPException(400, "not a knowledge-store op. Use /memory/restore")
    return op


def _undo(op):
    """Reverse one knowledge op via the gated API. Returns a short result dict."""
    t = op.get("type")
    before = op.get("before") or []
    if t == "gc":
        # re-create each GC'd entity from its snapshot (propose->commit => audited)
        for e in before:
            ks.commit(ks.propose("set", entity_id=e["id"], type=e.get("type"), name=e.get("name"),
                                 attrs=e.get("attrs") or {}, source="restore", actor="owner")["token"])
        return {"restored": "gc", "entities": [e["id"] for e in before]}
    if t == "merge":
        # un-merge: bring the superseded members back as their own records (canonical keeps its
        # losslessly-filled attrs — re-setting it adds nothing, so this never loses data)
        for e in before:
            ks.commit(ks.propose("set", entity_id=e["id"], type=e.get("type"), name=e.get("name"),
                                 attrs=e.get("attrs") or {}, source="restore", actor="owner")["token"])
        return {"restored": "merge", "entities": [e["id"] for e in before]}
    if t in ("promote", "codify"):
        after = op.get("after") or {}
        return {"restored": "promote", **ks.uncodify(after.get("type"), by="owner")}
    raise HTTPException(400, f"cannot restore knowledge op type '{t}'")


@router.post("/knowledge/restore", tags=["knowledge"])
async def restore(b: RestoreBody):
    op = _load_op(b)
    if gcm.stale_snapshot(op):
        return {"ok": False, "stale": True,
                "note": "this was changed again since, review before restoring"}
    return {"ok": True, **_undo(op)}


@router.post("/knowledge/reject", tags=["knowledge"])
async def reject(b: RestoreBody):
    """Reject = undo + don't-redo: reverse the op AND suppress it so the next groom run skips it."""
    op = _load_op(b)
    if gcm.stale_snapshot(op):
        return {"ok": False, "stale": True,
                "note": "this was changed again since, review before rejecting"}
    res = _undo(op)
    key = gcm.suppress("knowledge", op["type"], gcm.op_identity(op),
                       reason=op.get("reason") or "rejected by the owner")
    return {"ok": True, "rejected": True, "suppression": key, **res}
