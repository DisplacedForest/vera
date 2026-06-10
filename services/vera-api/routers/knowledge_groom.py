"""Nightly knowledge-store grooming — the counterweight to the store's fast-landing write path.

The flexible knowledge core lets facts land fast and a little messy during the day; this pass trends
it back toward clean overnight without ever slowing a write. It is the sibling of the vera-memory
groomer — same pattern, different store.

It touches the store ONLY through the knowledge store's gated API: a merge is a `set` on the canonical
entity plus a `delete` of each superseded member (propose->commit), and a promotion is `promote()`. So
every change lands in the append-only `revision` log — same audit, same rollback as a human write.

Tiered autonomy (mirrors the store's propose->commit gate):
- auto-apply (safe, reversible): GC, high-confidence lossless dedups, stable type promotions.
- propose-for-review (lossy/ambiguous): merges that would discard data, borderline promotions, conflicts.
Review items become Pulse cards; nothing lossy is applied silently.

POST /knowledge/groom_pass (gated, X-Agent-Token). dry_run reports intended actions and changes nothing.
Kill switch: KNOWLEDGE_GROOM_ENABLED=false. Idempotent: a re-run on a groomed store is a no-op (propose
tokens are content hashes, merged members are gone, promoted types are skipped).
"""
import json
import os
import time

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from . import groom_common as gcm
from . import knowledge_store as ks
from .pulse import _inject, _vera

router = APIRouter()
AGENT_TOKEN = os.environ.get("KNOWLEDGE_AGENT_TOKEN", "")

MAX_REVIEW_CARDS = 10  # bound the nightly card volume; overflow is logged, never silently dropped

DEDUP_SYS = (
    "You are the home knowledge-store groomer. Below are clusters of entities that look similar. For "
    "EACH cluster, decide which entities are genuinely the SAME real-world thing and should be merged "
    "into one canonical record, and which only look alike and must stay separate. Be conservative: if "
    "you are not sure two entities are the same physical thing, do NOT merge them.\n"
    "For every group you are confident about, name the canonical id (the most complete / best-named "
    "record) and the member ids to fold into it (the canonical id may appear in members; it is ignored).\n"
    "Respond ONLY with JSON: {\"merge\":[{\"canonical\":\"id\",\"members\":[\"id\",...]}]}. "
    "Use {\"merge\":[]} if nothing should be merged."
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
    """The knowledge-store grooming work (GC + dedup + promote + flags) WITHOUT injecting any
    Pulse card — returns its reversible change-set + review proposals so a caller (the standalone
    endpoint, or the unified groom session) decides how to surface it. Separates 'do the work'
    from 'tell the human'; the model's dedup judgment (DEDUP_SYS) is unaffected."""
    out = {
        "ok": True, "dry_run": dry_run, "errors": [], "run_id": run_id,
        "gc": {"pending": 0, "orphans": []},
        "merged": [], "promoted": [],
        "review": [],  # propose-for-review: lossy merges + borderline promotions
        "flagged": {"conflicts": [], "stale": [], "empty_types": []},
        "change_set": [],
    }
    change_set = out["change_set"]
    b = type("B", (), {"dry_run": dry_run})()  # local shim so the existing body reads unchanged

    # 1. GC (auto, safe) -----------------------------------------------------------------------
    orphans = ks.orphan_entities()
    out["gc"]["orphans"] = [o["id"] for o in orphans]
    if not b.dry_run:
        out["gc"]["pending"] = ks.sweep_pending()
        for o in orphans:
            if gcm.is_suppressed("knowledge", "gc", o["id"]):
                continue  # a prior Reject said keep this one
            try:
                ks.commit(ks.propose("delete", entity_id=o["id"], type=o["type"], name=o["name"],
                                     source="groom", actor="coder")["token"])
                change_set.append(gcm.op("gc", "knowledge", "removed orphan (no attributes left)",
                                         run_id=run_id, before=[gcm.snap_entity(o)], after=None))
            except Exception as e:
                out["errors"].append(f"gc {o['id']}: {e}")

    # 2. Dedup / merge -------------------------------------------------------------------------
    clusters = ks.dedup_clusters()
    if clusters:
        usr = "\n\n".join(
            f"Cluster {i} (type={c[0]['type']}):\n" + "\n".join(
                f"- id={e['id']} | {e['name']} | attrs={json.dumps(e['attrs'], sort_keys=True)}"
                for e in c)
            for i, c in enumerate(clusters))
        try:
            decision = _parse_json(await _vera(
                [{"role": "system", "content": DEDUP_SYS}, {"role": "user", "content": usr}],
                temperature=0.2))
        except Exception as e:
            out["errors"].append(f"dedup decide: {e}")
            decision = {}

        known = {e["id"] for c in clusters for e in c}
        for grp in (decision.get("merge") or []):
            if not isinstance(grp, dict):
                continue
            canonical = grp.get("canonical")
            members = [i for i in (grp.get("members") or []) if i in known and i != canonical]
            if canonical not in known or not members:
                continue
            entities = [ks.get(i) for i in [canonical] + members]
            entities = [e for e in entities if e]
            conflicts = ks.cluster_conflicts(entities)
            if conflicts:
                # lossy: would have to discard a contradictory value -> review, do not apply
                item = {"kind": "merge", "canonical": canonical, "members": members,
                        "conflicts": conflicts}
                out["review"].append(item)
                out["flagged"]["conflicts"].append({"entities": [e["id"] for e in entities],
                                                     "conflicts": conflicts})
            else:
                ident = "+".join(sorted(e["id"] for e in entities))
                if gcm.is_suppressed("knowledge", "merge", ident):
                    continue  # a prior Reject said keep these separate
                plan = ks.apply_merge(canonical, members, by="coder", dry_run=b.dry_run)
                if plan.get("ok"):
                    out["merged"].append({"canonical": canonical, "superseded": plan["superseded"]})
                    if not b.dry_run:
                        after_ent = ks.get(canonical)
                        change_set.append(gcm.op(
                            "merge", "knowledge", "folded duplicate records of the same thing",
                            run_id=run_id,
                            before=[gcm.snap_entity(e) for e in entities],
                            after=gcm.snap_entity(after_ent) if after_ent else None))

    # 3. Type promotion ------------------------------------------------------------------------
    for cand in ks.promotion_candidates():
        stable = cand["coverage"] >= 0.9 and cand["entities"] >= 3
        if not stable:
            out["review"].append({"kind": "promote", "type": cand["type"],
                                  "coverage": cand["coverage"], "entities": cand["entities"]})
            continue
        if gcm.is_suppressed("knowledge", "promote", cand["type"]):
            continue  # a prior Reject said don't codify this type
        if b.dry_run:
            out["promoted"].append({"type": cand["type"], "entities": cand["entities"]})
            continue
        migrated = ks.query(type=cand["type"], limit=100000)  # the entities this schema will govern
        res = ks.promote(cand["type"], cand["schema"], by="coder")
        if res.get("ok"):
            out["promoted"].append({"type": cand["type"], "migrated": res["migrated"]})
            change_set.append(gcm.op(
                "promote", "knowledge", "codified a stabilized type's schema", run_id=run_id,
                before=[gcm.snap_type(cand["type"], None, [])],
                after=gcm.snap_type(cand["type"], cand["schema"], migrated)))
        else:
            out["errors"].append(f"promote {cand['type']}: invalid entities")
            out["review"].append({"kind": "promote", "type": cand["type"],
                                  "invalid": res.get("invalid")})

    # 4. Flags (surface for a human, never auto-resolved) --------------------------------------
    out["flagged"]["stale"] = ks.stale_entities()
    out["flagged"]["empty_types"] = ks.empty_types()
    out["acted"] = bool(out["merged"] or out["promoted"] or out["review"]
                        or out["gc"]["pending"] or out["gc"]["orphans"])
    return out


def summary_parts(out):
    """The short human summary fragments for a knowledge-groom result (shared by the standalone
    card and the unified session digest)."""
    parts = []
    if out["merged"]:
        parts.append(f"merged {len(out['merged'])}")
    if out["promoted"]:
        parts.append(f"promoted {len(out['promoted'])}")
    if out["review"]:
        parts.append(f"flagged {len(out['review'])} for review")
    gc_n = out["gc"]["pending"] + len(out["gc"]["orphans"])
    if gc_n:
        parts.append(f"GC'd {gc_n}")
    return parts


@router.post("/knowledge/groom_pass", tags=["knowledge"])
async def groom_pass(b: GroomPass, x_agent_token: str = Header(default="")):
    if not AGENT_TOKEN or x_agent_token != AGENT_TOKEN:
        raise HTTPException(403, "groom_pass requires X-Agent-Token")
    if os.environ.get("KNOWLEDGE_GROOM_ENABLED", "true").lower() == "false":
        return {"ok": True, "disabled": True}

    run_id = b.run_id or str(int(time.time()))
    out = await run_pass(b.dry_run, run_id)
    if b.dry_run:
        return out  # intended actions only; nothing written, no cards

    # Pulse: per-review cards (bounded) + one run-summary status card --------------------------
    shown = out["review"][:MAX_REVIEW_CARDS]
    overflow = len(out["review"]) - len(shown)
    for r in shown:
        try:
            if r["kind"] == "merge":
                title = "Possible duplicate to reconcile"
                body = (f"I found records that look like the same thing but disagree, so I left them "
                        f"alone: `{r['canonical']}` and {', '.join(f'`{m}`' for m in r['members'])}. "
                        f"Conflicting facts: {json.dumps(r['conflicts'])}. Want me to reconcile them?")
            else:
                title = f"Type ready to codify: {r['type']}"
                body = (f"The `{r['type']}` type looks like it's stabilizing "
                        f"({r.get('entities', '?')} entities, {r.get('coverage', '?')} coverage) but "
                        f"isn't a confident auto-promote yet. Want me to codify its schema?")
            await _inject(title, body, summary=title, kind="status", severity=None,
                          category="vera", provenance="scheduled")
        except Exception as e:
            out["errors"].append(f"review card: {e}")
    if overflow:
        out["errors"].append(f"review cards capped at {MAX_REVIEW_CARDS}; {overflow} not shown")

    summary = "; ".join(summary_parts(out)) or "nothing to groom"
    try:
        await _inject(
            f"Groomed the knowledge store · {summary}",
            f"Overnight I tidied the home knowledge store: {summary}. Everything I changed is in the "
            f"revision log and reversible; anything ambiguous I left for you to confirm.",
            summary=summary, kind="status", severity=None, category="vera",
            provenance="scheduled", change_set=out["change_set"] or None)
        out["card"] = True
    except Exception as e:
        out["errors"].append(f"summary card: {e}")

    return out
