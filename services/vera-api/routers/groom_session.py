"""Unified nightly grooming session — one coordinated run, one digest card.

Tending the world-model and the knowledge store on independent triggers would give a single
night 3–4 separate Pulse cards that could even contradict each other (a belief promoted to
core while its near-duplicate was archived). This session is the
*coordinator*: it runs the existing intelligent passes IN ORDER, sharing one run id and accumulating
one change-set, then surfaces a single "Last night I tended my knowledge" digest.

Coordinate, do NOT collapse: each pass keeps its agentic coder-model judgment (CURATE_SYS / DEDUP_SYS)
untouched — we only sequence them so dedup/merge precedes promote, and fault-isolate each so one
failing pass never aborts the night.

POST /memory/self/groom_session (gated, X-Agent-Token). dry_run reports intended actions, no card.
"""
import os
import time

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from . import groom_common as gcm
from . import knowledge_groom as kg
from . import vera_memory_groom as vmg
from .actions import _stage
from .pulse import _inject

router = APIRouter()
AGENT_TOKEN = os.environ.get("KNOWLEDGE_AGENT_TOKEN", "")


class SessionBody(BaseModel):
    dry_run: bool = False


def _flagged_items(review):
    """Turn knowledge-groom review proposals into digest items for the card's 'Flagged for review'
    section. Borderline PROMOTE proposals get a one-click Approve (a staged knowledge.promote action)
    + Reject (suppress on skip). Lossy MERGE proposals can't be one-click reconciled — they render as
    info rows that point the human to chat."""
    items = []
    for i, r in enumerate(review):
        if r.get("kind") == "promote" and r.get("type") and r.get("schema") is not None:
            ok, err = _stage("knowledge.promote", {"type": r["type"], "schema": r["schema"]},
                             "scheduled", "vera", None, None)
            if err:
                continue
            items.append({
                "item_id": f"review:promote:{r['type']}",
                "title": f"Codify '{r['type']}' type?",
                "subtitle": f"{r.get('entities', '?')} entities, {r.get('coverage', '?')} coverage. "
                            f"Stabilizing but not a confident auto-promote.",
                "group": "Flagged for review", "state": "pending",
                "action": {"verb": "knowledge.promote", "args": {"type": r["type"], "schema": r["schema"]},
                           "token": ok["token"], "preview": ok["preview"], "risk": ok["risk"],
                           "reversible": ok["reversible"]},
            })
        elif r.get("kind") == "promote":  # invalid-entities promote — informational
            items.append({"item_id": f"review:promote:{r.get('type')}", "title": f"'{r.get('type')}' not ready",
                          "subtitle": "some entities don't fit the schema yet", "group": "Flagged for review",
                          "state": "info"})
        elif r.get("kind") == "merge":
            members = ", ".join([r.get("canonical", "")] + (r.get("members") or []))
            items.append({"item_id": f"review:merge:{i}", "title": "Possible duplicate to reconcile",
                          "subtitle": f"{members} disagree on: {', '.join((r.get('conflicts') or {}).keys())}"
                                      ". Discuss in chat", "group": "Flagged for review", "state": "info"})
    return items


@router.post("/memory/self/groom_session", tags=["vera_memory"])
async def groom_session(b: SessionBody, x_agent_token: str = Header(default="")):
    if not AGENT_TOKEN or x_agent_token != AGENT_TOKEN:
        raise HTTPException(403, "groom_session requires X-Agent-Token")

    run_id = str(int(time.time()))
    report = {"ok": True, "run_id": run_id, "dry_run": b.dry_run, "errors": [], "passes": {}}
    change_set = []
    summary = []

    # 1. World-model (dedup/merge precede promote INSIDE the pass — coder-judged). Fault-isolated.
    try:
        mem = await vmg.run_pass(b.dry_run, run_id)
        change_set += mem.get("change_set") or []
        report["errors"] += mem.get("errors") or []
        report["passes"]["memory"] = {"acted": mem.get("acted"), "summary": vmg.summary_parts(mem)}
        summary += vmg.summary_parts(mem)
    except Exception as e:
        report["errors"].append(f"memory pass: {e}")

    # 2. Knowledge store. Fault-isolated.
    review = []
    try:
        kn = await kg.run_pass(b.dry_run, run_id)
        change_set += kn.get("change_set") or []
        review = kn.get("review") or []
        report["errors"] += kn.get("errors") or []
        report["passes"]["knowledge"] = {"acted": kn.get("acted"), "summary": kg.summary_parts(kn)}
        summary += kg.summary_parts(kn)
    except Exception as e:
        report["errors"].append(f"knowledge pass: {e}")

    items = _flagged_items(review) if not b.dry_run else []
    report["change_set"] = change_set
    report["review_count"] = len(review)
    report["items"] = items

    if b.dry_run:
        return report

    summary_str = "; ".join(summary) or "nothing to tend"
    if change_set or items:
        try:
            res = await _inject(
                f"Last night I tended my knowledge · {summary_str}",
                f"Overnight I tended my world-model and the home knowledge store: {summary_str}. "
                f"Everything I changed is reversible. Restore or reject anything below; anything "
                f"ambiguous I left flagged for you to confirm.",
                summary=summary_str, kind="status", severity=None, category="vera",
                provenance="scheduled", change_set=change_set or None, items=items or None)
            report["card"] = True
            report["card_id"] = res.get("id")
        except Exception as e:
            report["errors"].append(f"digest card: {e}")

    return report
