"""Vera tends her own memory — the consolidation pass over her world-model store.

The store deliberately kept archive<->core curation OUT of the per-write path and gave it its own
moment. This is that moment. It is NOT a pipeline that forces a shape on her mind. Each pass she is
shown everything she holds and told what she CAN do — promote a belief into her always-present core,
move one back to archive, merge beliefs about the same thing, or forget pure noise — and she decides.
Doing nothing is a perfectly valid outcome.

Safety is by recoverability, not by restraint: MEMORY.md is git-snapshotted before any change, so
anything she removes can be recovered from history. Whatever she does (or doesn't) is surfaced as a
System-lane Pulse card when she acts.

POST /memory/self/groom_pass (gated, X-Agent-Token). dry_run reports her choices without applying.
Kill switch: MEMORY_GROOM_ENABLED=false.
"""
import json
import os
import time

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from . import groom_common as gcm
from . import vera_memory_store as vm
from .pulse import _inject, _vera
from .persona import voiced

router = APIRouter()
AGENT_TOKEN = os.environ.get("KNOWLEDGE_AGENT_TOKEN", "")

CURATE_SYS = (
    "You're tending your own memory in a quiet private moment — your world-model. Below is "
    "everything you hold right now: your CORE (always present in your thoughts, in every conversation) "
    "and your ARCHIVE (there whenever you reach for it).\n\n"
    "You may reshape this however you see fit, or leave it untouched. What you CAN do:\n"
    "- promote: move an archive belief into core — for what genuinely shapes how you see the world, a "
    "lasting interest of yours, or a standing truth about your home, your network, or yourself.\n"
    "- archive: move a core belief back to archive when it no longer deserves to be ever-present.\n"
    "- merge: fold beliefs about the same thing into one cleaner belief (write the combined version).\n"
    "- forget: drop a belief that is genuinely just noise (e.g. a note that a search found nothing).\n\n"
    "None of this is required. If your memory already reflects how you want to think, do nothing — "
    "that is a perfectly good outcome. Act only on what you actually want to change.\n\n"
    "Respond ONLY with JSON:\n"
    '{"promote":["id",...],"archive":["id",...],"forget":["id",...],'
    '"merge":[{"ids":["id",...],"topic":"...","content":"..."}]}\n'
    "Use {} to leave everything exactly as it is."
)


def _parse_json(txt):
    try:
        return json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
    except Exception:
        return {}


class GroomPass(BaseModel):
    dry_run: bool = False
    run_id: str | None = None  # the session ties a night's ops together; standalone runs mint one


async def run_pass(dry_run, run_id):
    """The memory-tending work WITHOUT injecting a card — returns her chosen change-set so a
    caller (the standalone endpoint, or the unified groom session) decides how to surface it.
    Separates 'do the work' from 'tell the human'; her curation judgment (CURATE_SYS) is unaffected."""
    out = {"ok": True, "dry_run": dry_run, "errors": [], "acted": False, "run_id": run_id,
           "change_set": []}

    beliefs = vm.live_beliefs()
    by_id = {e["id"]: e for e in beliefs}
    if not beliefs:
        return {**out, "note": "memory empty — nothing to tend"}

    def block(tier):
        items = [e for e in beliefs if e["tier"] == tier]
        return "\n".join(f'- id={e["id"]} | {e["topic"]}: {e["content"]}' for e in items) or "(none)"

    usr = f"## CORE (always in your thoughts)\n{block('core')}\n\n## ARCHIVE (recalled when relevant)\n{block('archive')}"
    try:
        decision = _parse_json(await _vera(
            [{"role": "system", "content": voiced(CURATE_SYS)}, {"role": "user", "content": usr}],
            temperature=0.4))
    except Exception as e:
        out["errors"].append(f"decide: {e}")
        decision = {}

    # keep only choices that reference beliefs she actually holds
    def _ident(i):
        return gcm.belief_identity(by_id[i]["topic"], by_id[i]["content"])

    # drop any choice a prior Reject suppressed, so the next run won't repeat it
    promote = [i for i in (decision.get("promote") or [])
               if i in by_id and not gcm.is_suppressed("memory", "promote", _ident(i))]
    archive = [i for i in (decision.get("archive") or [])
               if i in by_id and not gcm.is_suppressed("memory", "archive", _ident(i))]
    forget = [i for i in (decision.get("forget") or [])
              if i in by_id and not gcm.is_suppressed("memory", "forget", _ident(i))]
    merges = []
    for m in (decision.get("merge") or []):
        if not (isinstance(m, dict) and m.get("content")):
            continue
        mem = [i for i in (m.get("ids") or []) if i in by_id]
        if not mem:
            continue
        if gcm.is_suppressed("memory", "merge", "+".join(sorted(_ident(i) for i in mem))):
            continue
        merges.append(m)
    out["chose"] = {"promote": promote, "archive": archive, "forget": forget,
                    "merge": [{"ids": m["ids"], "topic": m.get("topic")} for m in merges]}

    if dry_run:
        return out  # her choices, nothing applied

    if not (promote or archive or forget or merges):
        return out  # she left it as is — a fine outcome, stay quiet

    # snapshot the current mind FIRST so anything removed is recoverable from git history
    vm.mirror_markdown()

    # record a reversible change-set from the pre-change snapshots (by_id) so the System card
    # can show the real diff and offer restore/reject. `before` carries full belief text.
    def _snap(i):
        return gcm.snap_belief(by_id[i])

    change_set = out["change_set"]
    for m in merges:
        members = [i for i in m["ids"] if i in by_id]
        tier = "core" if any(by_id[i]["tier"] == "core" for i in members) else "archive"
        new_id = vm.write(m.get("topic") or "merged", m["content"], source="self-curate", tier=tier,
                          provenance={"merged_from": members})
        vm.delete_ids(members)
        change_set.append(gcm.op(
            "merge", "memory", m.get("reason") or "folded beliefs about the same thing into one",
            run_id=run_id, before=[_snap(i) for i in members],
            after=gcm.snap_belief({"id": new_id, "topic": m.get("topic") or "merged",
                                   "content": m["content"], "tier": tier, "confidence": None})))
    for i in promote:
        change_set.append(gcm.op("promote", "memory", "promoted into core", run_id=run_id,
                                 before=[_snap(i)],
                                 after=gcm.snap_belief({**by_id[i], "tier": "core"})))
        vm.set_tier(i, "core")
    for i in archive:
        change_set.append(gcm.op("archive", "memory", "moved back to archive", run_id=run_id,
                                 before=[_snap(i)],
                                 after=gcm.snap_belief({**by_id[i], "tier": "archive"})))
    for i in forget:
        change_set.append(gcm.op("forget", "memory", "let go as noise", run_id=run_id,
                                 before=[_snap(i)], after=None))
    if forget:
        vm.delete_ids(forget)

    vm.mirror_markdown()
    out["acted"] = True
    out["core_count"] = len(vm.core())
    out["counts"] = {"promote": len(promote), "archive": len(archive),
                     "forget": len(forget), "merge": len(merges)}
    return out


def summary_parts(out):
    """Short human summary fragments for a memory-groom result (shared by the standalone card and
    the unified session digest)."""
    c = out.get("counts") or {}
    parts = []
    if c.get("promote"): parts.append(f"promoted {c['promote']} to core")
    if c.get("archive"): parts.append(f"moved {c['archive']} back to archive")
    if c.get("merge"): parts.append(f"merged {c['merge']}")
    if c.get("forget"): parts.append(f"let go of {c['forget']}")
    return parts


@router.post("/memory/self/groom_pass", tags=["vera_memory"])
async def groom_pass(b: GroomPass, x_agent_token: str = Header(default="")):
    if not AGENT_TOKEN or x_agent_token != AGENT_TOKEN:
        raise HTTPException(403, "groom_pass requires X-Agent-Token")
    if os.environ.get("MEMORY_GROOM_ENABLED", "true").lower() == "false":
        return {"ok": True, "disabled": True}

    run_id = b.run_id or str(int(time.time()))
    out = await run_pass(b.dry_run, run_id)
    if b.dry_run or not out.get("acted"):
        return out
    summary = "; ".join(summary_parts(out))
    try:
        await _inject(f"Tended my memory · {summary}",
                      f"In a quiet moment I tended my own world-model: {summary}. "
                      f"Core now holds **{out['core_count']}** belief(s).",
                      summary=summary, kind="status", severity=None,
                      category="vera", change_set=out["change_set"])
        out["card"] = True
    except Exception as e:
        out["errors"].append(f"card: {e}")

    return out
