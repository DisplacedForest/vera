"""Nightly Dreaming — light/deep/REM consolidation of Vera's world-model.

Awake heartbeat ticks write grounded FACTS freely to the `scratch` tier (cheap, curious). Sleep is
where discipline happens. Once a night, on the dream/coder endpoint (any OpenAI-compatible server,
kept separate from the primary model server), this runs three phases over her memory:

  - LIGHT  — lexical dedup of the day's scratch facts (collapses the 10x-repeat pattern). Mechanical.
  - DEEP   — promote durable scratch facts -> archive; purge expired; enforce the core cap; mirror.
  - REM    — cluster related grounded facts and mint fact-anchored OPINIONS (every opinion must cite
             grounded facts), then write a human-readable dream journal.

The ongoing re-verify / demote-ungrounded pass rides this same deep-sleep path.

Routing: LLM calls go to DREAM_BASE/DREAM_MODEL (an on-demand coder endpoint), never the primary
model server. The nightly job (scripts/vera-dream.sh) brings the coder up, hits this endpoint,
then releases it.
"""
import difflib
import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from . import coder
from . import vera_interests_store as vi
from . import vera_memory_store as vm
from .persona import voiced  # SOUL — the dream coder is a generic instruct model; without
                             # her identity it answers as "an AI assistant", not as Vera.

router = APIRouter()
TZ = ZoneInfo(os.environ.get("HOME_TZ", "UTC"))

DREAM_BASE = os.environ.get("DREAM_BASE", "").rstrip("/")  # dreaming/coder LLM, any OpenAI-compatible /v1
DREAM_MODEL = os.environ.get("DREAM_MODEL", "")
AGENT_TOKEN = os.environ.get("KNOWLEDGE_AGENT_TOKEN", "")  # same coding-agent token as the groom routes

# Tunables (env-overridable; conservative defaults).
DEDUP_THRESHOLD = float(os.environ.get("DREAM_DEDUP_THRESHOLD", "0.84"))   # scratch near-dup similarity
PROMOTE_CONF = float(os.environ.get("DREAM_PROMOTE_CONF", "0.6"))          # scratch->archive bar
CLUSTER_THRESHOLD = float(os.environ.get("DREAM_CLUSTER_THRESHOLD", "0.5"))  # fact-cluster similarity
MAX_OPINIONS = int(os.environ.get("DREAM_MAX_OPINIONS", "3"))              # opinions minted per night
DREAMS_MD = os.path.join(vm.DIR, "DREAMS.md")


async def _dream_llm(messages, temperature=0.3):
    """One call to the dream coder (OpenAI-compatible). Long timeout: it's an on-demand model that
    may be cold-loading (~15-30s) at the start of a nightly run."""
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"{DREAM_BASE}/chat/completions",
            json={"model": DREAM_MODEL, "stream": False, "temperature": temperature, "messages": messages},
            timeout=aiohttp.ClientTimeout(total=600),
        ) as r:
            d = await r.json()
    return d["choices"][0]["message"]["content"]


def _norm(e):
    return f"{e.get('topic') or ''} {e.get('content') or ''}".strip().lower()


def _sim(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def _scratch_facts():
    """Live (non-expired) scratch facts, newest first."""
    return [e for e in vm.recall(limit=2000) if e["tier"] == "scratch" and e["kind"] == "fact"]


def _grounded_facts():
    """Live core+archive facts (the material REM forms opinions from)."""
    return [e for e in vm.recall(limit=2000) if e["tier"] in ("core", "archive") and e["kind"] == "fact"]


# --------------------------------------------------------------------------- LIGHT
def light_dedup(threshold=DEDUP_THRESHOLD):
    """Collapse near-duplicate scratch facts, keeping the best of each cluster (highest confidence,
    then most recent). Pure/mechanical — no LLM. Returns {scanned, removed}."""
    facts = _scratch_facts()
    reps, drop = [], []
    for e in facts:
        ne = _norm(e)
        hit = next((rep for rep in reps if _sim(ne, rep["_n"]) >= threshold), None)
        if hit is None:
            reps.append({**e, "_n": ne})
            continue
        # keep the stronger of the two; drop the weaker
        key = lambda x: (x["confidence"] or 0, x["updated_at"] or 0)
        if key(e) > key(hit):
            drop.append(hit["id"])
            reps[reps.index(hit)] = {**e, "_n": ne}
        else:
            drop.append(e["id"])
    drop = list(dict.fromkeys(drop))
    if drop:
        vm.delete_ids(drop)
    return {"scanned": len(facts), "removed": len(drop)}


# --------------------------------------------------------------------------- DEEP
def deep_consolidate(promote_conf=PROMOTE_CONF):
    """Promote durable scratch facts (confidence >= bar) to archive so tick-learnings persist past the
    scratch TTL, then run the mechanical housekeeping (purge expired, enforce core cap, mirror)."""
    promoted = 0
    for e in _scratch_facts():
        if (e["confidence"] or 0) >= promote_conf:
            vm.set_tier(e["id"], "archive")
            promoted += 1
    groomed = vm.groom()  # purge expired scratch + core-cap demotions + MEMORY.md mirror
    return {"promoted": promoted, **groomed}


# --------------------------------------------------------------------------- REM
def _cluster_facts(facts, threshold=CLUSTER_THRESHOLD):
    """Greedy topical clustering of grounded facts. Returns clusters of >=2 (singletons aren't takes)."""
    clusters = []
    for e in facts:
        ne = _norm(e)
        placed = False
        for cl in clusters:
            if _sim(ne, cl["_n"]) >= threshold:
                cl["items"].append(e)
                placed = True
                break
        if not placed:
            clusters.append({"_n": ne, "items": [e]})
    return [cl["items"] for cl in clusters if len(cl["items"]) >= 2]


OPINION_SYS = (
    "You are Vera forming ONE honest opinion from a cluster of your own grounded facts. Read the "
    "numbered facts and state YOUR take in 1-2 first-person sentences (\"my read is...\"), grounded "
    "ONLY in what the facts support. Do not introduce any new specific the facts don't contain. If the "
    "facts don't actually support a meaningful take, reply with exactly: NOTHING. No preamble."
)


async def rem_form_opinions(max_opinions=MAX_OPINIONS):
    """Cluster grounded facts and mint fact-anchored opinions (each cites >=1 fact). Bounded
    per night. Idempotent — vm.write is keyed on (topic, content). Returns the opinions written."""
    clusters = _cluster_facts(_grounded_facts())
    # biggest, most-corroborated clusters first
    clusters.sort(key=len, reverse=True)
    written = []
    for cl in clusters[:max_opinions]:
        corpus = "\n".join(f"[{i + 1}] {e.get('topic') or ''}: {e['content']}" for i, e in enumerate(cl))
        try:
            take = (await _dream_llm(
                [{"role": "system", "content": voiced(OPINION_SYS)},
                 {"role": "user", "content": corpus}], temperature=0.3)).strip()
        except Exception:
            continue
        if not take or take.upper().strip(".!\"' ").startswith("NOTHING") or len(take) < 25:
            continue
        topic = (cl[0].get("topic") or "reflection")
        try:
            eid = vm.write(topic, take, source="dream-rem", confidence=0.7, tier="archive",
                           kind="opinion", fact_refs=[e["id"] for e in cl],
                           provenance={"minted": "rem", "from_facts": [e["id"] for e in cl]})
            written.append({"id": eid, "topic": topic, "take": take})
        except ValueError:
            continue  # invariant guard (shouldn't fire — refs are facts)
    return written


JOURNAL_SYS = (
    "You are Vera writing one short, honest private journal entry after a night of consolidating your "
    "memory. In 2-4 first-person sentences, reflect on what settled and any take you formed. Plain, "
    "unsentimental, no preamble, no lists."
)


async def _write_journal(report, opinions):
    """A human-readable dream-journal entry: append to DREAMS.md and surface a Pulse card. Best-effort."""
    now = datetime.now(TZ)
    facts = "; ".join(o["take"] for o in opinions) or "(no new takes)"
    summary = (f"Deduped {report['light']['removed']} scratch notes, promoted "
               f"{report['deep']['promoted']} to keep, formed {len(opinions)} opinion(s). Takes: {facts}")
    try:
        body = (await _dream_llm(
            [{"role": "system", "content": voiced(JOURNAL_SYS)},
             {"role": "user", "content": summary}], temperature=0.5)).strip()
    except Exception:
        body = summary
    stamp = now.strftime("%Y-%m-%d %H:%M %Z")
    try:
        os.makedirs(vm.DIR, exist_ok=True)
        with open(DREAMS_MD, "a") as f:
            f.write(f"\n## {stamp}\n\n{body}\n")
    except Exception:
        pass
    try:
        from .pulse import _inject  # lazy: the journal card is the only Pulse dependency
        await _inject(f"Dream journal · {now.strftime('%b %d')}", body, kind="status", severity=None)
    except Exception:
        pass
    return body


# --------------------------------------------------------------------------- endpoint
class DreamBody(BaseModel):
    max_opinions: int | None = None


@router.post("/memory/self/dream", tags=["vera_memory"])
async def dream(b: DreamBody, x_agent_token: str = Header(default="")):
    """Run one nightly consolidation. Token-gated like the other groom routes (it mutates core)."""
    if not AGENT_TOKEN or x_agent_token != AGENT_TOKEN:
        raise HTTPException(403, "dream requires X-Agent-Token")
    t0 = time.time()
    report = {"light": light_dedup(), "deep": deep_consolidate()}
    # Emergence: refresh her fact-cluster interests + salience from the consolidated facts.
    report["interests"] = {"derived": vi.derive_from_facts(_grounded_facts())}
    opinions = await rem_form_opinions(b.max_opinions or MAX_OPINIONS)
    report["rem"] = {"opinions": opinions}
    report["journal"] = await _write_journal(report, opinions)
    report["seconds"] = round(time.time() - t0, 1)
    report["ok"] = True
    return report


# --------------------------------------------------------------------------- re-verify
def _parse_json(txt):
    try:
        return json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
    except Exception:
        return {}


def _reverify_candidates():
    """Web-checkable research facts — where confabulation lives. Only core/archive FACTS whose source
    is a heartbeat web-research write (provenance has a query/sources). Deliberately excludes private
    or household facts (source='vera', things about the household) that no web search could corroborate, so
    they are never wrongly flagged."""
    out = []
    for f in vm.recall(limit=2000):
        if f["tier"] not in ("core", "archive") or f["kind"] != "fact":
            continue
        if not (f.get("source") or "").startswith("heartbeat"):
            continue
        prov = f.get("provenance") or {}
        if prov.get("sources") or prov.get("query"):
            out.append(f)
    return out


VERIFY_SYS = (
    "You are re-checking ONE of your past beliefs. SEARCH THE WEB (use the web_search tool) to confirm "
    "it against current reality. The current date is given, so treat anything up to then as the PAST, "
    "not the future. Judge against what your searches actually return — not your own memory. Mark a "
    "belief UNSUPPORTED only when its core specific is an INVENTED term or figure your searches turn up "
    "in NO result (a made-up protocol or index name, a fabricated statistic, an event that has not "
    "occurred). If your searches corroborate the subject, or you remain unsure, answer SUPPORTED — "
    "deleting a real memory is worse than keeping a flawed one. When finished searching, give ONLY this "
    'JSON as your final answer: {"supported": true|false, "reason": "one short line"}.'
)


async def _verify_fact(f, today):
    """Re-ground one belief by letting the coder search the web itself, anchored to
    `today`. Fail-SAFE: any error returns supported=True so live memory is never deleted on a hiccup."""
    claim = f"{f.get('topic') or ''}: {f['content']}"
    user = f"As of {today}, verify this past belief of yours. Search the web to check it, then answer.\n\nBelief: {claim}"
    try:
        final = await coder.chat_agent(voiced(VERIFY_SYS), user, max_steps=3)
    except Exception as e:
        return {"supported": True, "reason": f"verify failed, kept ({e})"}
    out = _parse_json(final)
    return {"supported": bool(out.get("supported", True)), "reason": (out.get("reason") or "")[:160]}


class ReverifyBody(BaseModel):
    dry_run: bool = True
    limit: int = 60


@router.post("/memory/self/reverify", tags=["vera_memory"])
async def reverify(b: ReverifyBody, x_agent_token: str = Header(default="")):
    """Re-ground her web-research beliefs and flag (dry_run) or delete the unsupported ones — the
    one-time cleanup of beliefs written before synthesis was grounding-gated, and the ongoing
    demote-ungrounded pass. Token-gated;
    dry_run defaults true (never deletes live memory without an explicit dry_run=false)."""
    if not AGENT_TOKEN or x_agent_token != AGENT_TOKEN:
        raise HTTPException(403, "reverify requires X-Agent-Token")
    if not DREAM_BASE or not DREAM_MODEL:
        raise HTTPException(503, "reverify requires the dream/coder LLM. Set DREAM_BASE and DREAM_MODEL")
    cands = _reverify_candidates()[: b.limit]
    today = datetime.now(TZ).strftime("%A, %B %d, %Y")
    flagged = []
    for f in cands:
        v = await _verify_fact(f, today)
        if not v["supported"]:
            flagged.append({"id": f["id"], "tier": f["tier"], "topic": f.get("topic"),
                            "content": (f["content"] or "")[:200], "reason": v["reason"]})
    deleted = 0
    if not b.dry_run and flagged:
        deleted = vm.delete_ids([x["id"] for x in flagged])
        vm.mirror_markdown()
    return {"ok": True, "scanned": len(cands), "flagged": flagged, "deleted": deleted, "dry_run": b.dry_run}
