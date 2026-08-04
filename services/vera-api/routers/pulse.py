"""Pulse — proactive overnight briefings.

One endpoint, POST /pulse/run, does the whole pipeline:
  cleanup sweep -> gather memories -> triage (Vera) -> per-topic search + synthesize
  -> inject one card per topic into the Pulse store.

The scheduler's only job is to trigger this each morning.
"""

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from . import pulse_veins
from . import pulse_store as store
from . import user_profile_store as up
from . import vera_interests_store as vi
from . import pulse_images
from . import pulse_gates
from . import pulse_synthesis
from . import pulse_audit
from . import workflow_executor
from . import workflow_store
from . import pulse_nodes
from . import workflow_nodes
from .websearch import search as web_search
from .pulse_llm import (
    VERA_BASE, MODEL, TZ,
    _vera, _chat_payload, _get_memories, _active_users, _parse_template_kwargs,
)
from .pulse_images import (
    VERA_IMAGE_BASE, STYLE_PALETTE, IMAGE_SYS,
    image_protocol, _image_base, _image_registry, _image_request, _image_b64,
    _gen_image, _gather_images, _upload_image, _clean_caption, _vision, make_cover,
)
from .pulse_gates import (
    already_covered, is_stale_news, is_off_topic, _recent_for_user,
    _newest_published, _corpus_overview, _gate_kind, _GATE_MARKERS,
    DEDUP_SYS, FRESH_SYS, COHERENT_SYS,
)
from .pulse_synthesis import (
    TRIAGE_SYS, TRIAGE_RETRY, THREAD_SYS, CARD_SYS, SUMMARY_SYS,
    _numbered_corpus, _split_headline, _parse_threads, _synthesis_user_prompt,
    _select_topics, _triage, _source_adder,
    _collect_broad_sources, _deepen_sources, _synthesize_body, _summarize,
)
from .pulse_audit import (
    AUDIT_SYS, REVISE_SYS, _auditor, _parse_audit, audit_claims, _audit_hook, _audit_phase,
)

router = APIRouter()
log = logging.getLogger("vera.pulse")

# The delivery contract per run: keep re-triaging (bounded rounds) until at least the floor
# of NOVEL cards lands; the ceiling caps a run no matter how much looks interesting. The
# dedup gate never loosens — a gated topic costs the run a retry, not a card.
PULSE_MIN_CARDS = int(os.environ.get("PULSE_MIN_CARDS", "2"))
PULSE_MAX_CARDS = int(os.environ.get("PULSE_MAX_CARDS", "8"))
PULSE_TRIAGE_ROUNDS = int(os.environ.get("PULSE_TRIAGE_ROUNDS", "3"))
# Variety guarantee within one run: at most this many research cards per standing interest.
PULSE_MAX_PER_INTEREST = int(os.environ.get("PULSE_MAX_PER_INTEREST", "1"))

# Optional warm-up/release hooks bracketing the end-of-run claim-audit phase. When set,
# the run POSTs the wake URL before auditing (so an on-demand audit model can load once
# for the whole batch) and the release URL after — unless the wake reply said the model
# was already up, in which case it is not this run's to release. Unset = no hook calls;
# an always-resident audit endpoint needs neither.
AUDIT_WAKE_URL = os.environ.get("AUDIT_WAKE_URL", "").strip()
AUDIT_RELEASE_URL = os.environ.get("AUDIT_RELEASE_URL", "").strip()

CHAT_TEMPLATE_KWARGS = _parse_template_kwargs()


class PulseRequest(BaseModel):
    interests: list[str] = []
    max_cards: int | None = None  # explicit cap; defaults to PULSE_MAX_CARDS and never exceeds it
    sweep_only: bool = False  # run just the cleanup sweep, skip generation
    user_id: str | None = None    # who this briefing is for (defaults to the household owner)
    user_name: str | None = None  # display name for the briefing voice


async def _inject(title, body, image_url=None, tint=None, sources=None,
                  summary=None, inline_images=None, action=None, kind="research", severity=None,
                  user_id=None, provenance="scheduled", category=None, change_set=None, items=None,
                  situation_key=None):
    """Store a Pulse card. Compat shim for the helper routers (health/kitchen/weather/
    heartbeat) that surface cards.
    `kind`/`severity` place the card in an ambient vein; default is the research feed.
    `user_id` is the person the card is FOR; defaults to the household owner.
    `provenance` records how the card was triggered — "scheduled" vs "heartbeat".
    `items` carries a multi-item digest's per-row actions. Returns the new card id."""
    src = [{"n": s["n"], "title": s["title"], "url": s["url"]} for s in (sources or [])]
    imgs = [{"n": i, "url": im["url"], "caption": im.get("caption", ""), "sourceN": im.get("srcN", 0)}
            for i, im in enumerate(inline_images or [], start=1)]
    cid = str(uuid.uuid4())
    store.insert_card({
        "id": cid, "created_at": int(time.time()),
        "day": datetime.now(TZ).date().isoformat(), "status": "new",
        "title": title, "summary": summary or "", "body": body,
        "image_url": image_url, "tint": tint, "sources": src, "inline_images": imgs,
        "promoted_chat_id": None, "action": action, "kind": kind, "severity": severity,
        "user_id": user_id or store.DEFAULT_USER, "provenance": provenance,
        "category": category, "change_set": change_set, "items": items,
        "situation_key": situation_key,
    })
    return {"ok": True, "id": cid}


def _assemble_card(headline, topic, summary, body, image_url, tint, sources, inline_images,
                   provenance, user_id, audit_stamp):
    return {
        "id": str(uuid.uuid4()),
        "created_at": int(time.time()),
        "day": datetime.now(TZ).date().isoformat(),
        "status": "new",
        "title": headline or topic.get("title"),
        "summary": summary or "",
        "body": body,
        "image_url": image_url,
        "tint": tint,
        "sources": [{"n": s["n"], "title": s["title"], "url": s["url"]} for s in sources],
        "inline_images": [{"n": i, "url": im["url"], "caption": im["caption"], "sourceN": im.get("srcN", 0)}
                          for i, im in enumerate(inline_images, start=1)],
        "promoted_chat_id": None,
        "kind": "research",
        "provenance": provenance,
        "user_id": user_id,
        "audit": audit_stamp,
    }


async def research_topic(topic, *, who, user_id, idx=0, provenance="scheduled", errors=None,
                         defer_audit=False, outcome=None, style_profile="rotating"):
    """The per-topic deep-research pipeline: broad search -> thread extraction ->
    follow-up searches -> real imagery -> first-person synthesis -> summary -> cover art -> inject.

    Extracted from the /pulse/run loop so the scheduled morning briefing AND the heartbeat's
    for-you discovery create cards through ONE path (one feed, one bar). Returns the injected
    card dict, or None if synthesis produced nothing. `errors`, if given, collects the same
    non-fatal step-failure strings the run loop logs.

    `defer_audit=True` skips the inline claim audit and hands the full source corpus back on
    the returned card's ephemeral `_corpus` key (never persisted), so the run loop can audit
    the whole batch at end-of-run against one model wake. The card lands stamped
    `audit: none` until that phase overwrites it.

    `outcome`, if given, is a dict this fills with the structured result the run record keeps:
    on a gate kill `{gate, reason, detail}` (the same evidence the `errs` prose carries, kept
    as fields), and on a shipped card `{cover_generated}`. It mirrors the `errs` accumulator."""
    errs = errors if errors is not None else []
    oc = outcome if outcome is not None else {}

    # Dedup gate — if she's already produced a card for this, skip before spending any
    # research/image/synthesis on it. Catches the heartbeat AND scheduled paths (both land here).
    dup = await already_covered(topic, user_id)
    if dup:
        errs.append(f"skipped (already covered): {topic.get('title')} ≈ {dup['title']}")
        oc.update({"gate": "dedup", "reason": "already covered", "detail": dup.get("title")})
        return None

    # Numbered, deduped master source list accumulated across all searches.
    sources, url_to_n = [], {}
    add_sources = _source_adder(sources, url_to_n)
    await _collect_broad_sources(topic, add_sources)

    # Freshness gate — stale news skips like a dup, before any expensive work; the delivery
    # loop backfills the slot with a replacement topic.
    newest = _newest_published(sources)
    if await is_stale_news(topic, newest):
        errs.append(f"skipped (stale news): {topic.get('title')} — newest source {newest or 'undated'}")
        oc.update({"gate": "freshness", "reason": "stale news", "detail": newest or "undated"})
        return None

    # Coherence gate — a corpus that drifted to a different subject skips the same way;
    # a card must be about its topic, not about whatever the search happened to find.
    off, found = await is_off_topic(topic, sources)
    if off:
        errs.append(f"skipped (off-topic corpus): {topic.get('title')} — corpus about {found}")
        oc.update({"gate": "coherence", "reason": "off-topic corpus", "detail": found})
        return None

    threads = await _deepen_sources(topic, sources, add_sources, errs)

    # real imagery: image search on the key entity + og:images from top sources
    entity_query = (threads[0].get("focus") if threads else None) or topic.get("title")
    top_sources = [(s["n"], s["title"], s["url"]) for s in sources[:3]]
    try:
        inline_images = await _gather_images(idx, entity_query, top_sources)
    except Exception as e:
        inline_images = []
        errs.append(f"images {topic.get('title')}: {e}")

    headline, body = await _synthesize_body(topic, sources, who, inline_images)
    if not body:
        errs.append(f"skipped (empty synthesis): {topic.get('title')}")
        oc.update({"gate": "empty", "reason": "empty synthesis", "detail": None})
        return None  # nothing synthesized — don't inject an empty card

    # Cross-model claim validation — the coder audits the body against its own corpus and
    # Vera revises once, BEFORE cover art (so the art prompt sees the corrected body).
    # Deferred mode leaves the audit to the run's batched end-of-run phase, which amortizes
    # one audit-model wake across every card in the run.
    audit_stamp = "none"
    if not defer_audit:
        headline, body, audit_stamp, _audit_info = await audit_claims(headline, body, sources, errs, topic.get("title"))

    summary = await _summarize(body)

    image_url, tint, cover_generated = await make_cover(headline, summary, body, topic, inline_images, idx, errs, style_profile)
    oc["cover_generated"] = cover_generated

    card = _assemble_card(headline, topic, summary, body, image_url, tint, sources, inline_images,
                          provenance, user_id, audit_stamp)
    store.insert_card(card)
    if defer_audit:
        card["_corpus"] = sources  # full texts for the end-of-run audit; never persisted
    return card


def _stamp_interest(interest):
    """Put a shipped interest on the fixation cooldown so consecutive runs and for-you
    ticks rotate to something else. Best-effort — never blocks a card."""
    try:
        vi.observe(interest, source="chat", salience_bump=0.0)
        vi.touch(interest)
    except Exception:
        pass


async def _build_run_context(req, out):
    # Who is this briefing for? Default to the household owner for backward-compat.
    user_id = req.user_id or store.DEFAULT_USER
    profile = up.get(user_id)
    who = req.user_name or profile.get("name") or "them"

    # 1) gather + triage. Ground in this person's profile interests + whatever the caller passed.
    memories = []
    if user_id == store.DEFAULT_USER:
        try:
            memories = [m.get("content") for m in (await _get_memories() or []) if m.get("content")]
        except Exception as e:
            out["errors"].append(f"memories: {e}")

    all_interests = list(dict.fromkeys(list(req.interests) + [i["topic"] for i in profile.get("interests", [])]))
    # Fixation cooldown: an interest that just shipped a card (here or via a for-you tick)
    # sits out until its cooldown lapses, so consecutive runs rotate instead of replaying
    # whichever interest happens to search best.
    try:
        cooling = vi.cooled(all_interests)
    except Exception:
        cooling = set()
    if cooling:
        all_interests = [t for t in all_interests if t not in cooling]
    persona = profile.get("persona")
    return user_id, who, persona, all_interests, memories


def _workflow_node(workflow_definition, node_type):
    return next((node for node in (workflow_definition or {}).get("nodes", []) if node.get("type") == node_type), {})


def _finalize_run(out, gates, target):
    out["gates"] = gates
    if len(out["injected"]) < target and any(gates.values()):
        msg = (f"starved run: {len(out['injected'])}/{target} cards after "
               f"{len(out['rounds'])} triage round(s); gate kills: "
               + ", ".join(f"{k}={v}" for k, v in gates.items()))
        out["errors"].append(msg)
        log.warning("%s", msg)
    floor = min(PULSE_MIN_CARDS, target)  # an explicit small max_cards lowers the floor too
    if len(out["injected"]) < floor:
        out["errors"].append(
            f"under floor: {len(out['injected'])}/{floor} novel cards "
            f"after {len(out['rounds'])} triage round(s)")
    return out


async def _do_run(req: PulseRequest, workflow_definition: dict | None = None, workflow_run_id: str | None = None):
    out = {"ok": True, "topics": [], "injected": [], "skipped": [], "expired": 0, "errors": []}

    # 0) cleanup sweep (best-effort): expire untouched prior-day cards in the store
    try:
        out["expired"] = store.sweep(datetime.now(TZ).date().isoformat())
    except Exception as e:
        out["errors"].append(f"sweep: {e}")

    if req.sweep_only:
        return out

    user_id, who, persona, all_interests, memories = await _build_run_context(req, out)
    target = min(req.max_cards or PULSE_MAX_CARDS, PULSE_MAX_CARDS)
    definition = workflow_definition or workflow_store.baseline_definition()
    style = (_workflow_node(definition, "pulse.cover_art").get("config") or {}).get("style", "rotating")
    out["rounds"] = []
    out["items"] = []
    ctx = workflow_executor.RunContext(definition, run_id=workflow_run_id, data={
        "req": req, "out": out, "user_id": user_id, "who": who, "persona": persona,
        "interests": all_interests, "memories": memories, "target": target,
        "style": style, "run_id": workflow_run_id,
        "gates": {"dedup": 0, "freshness": 0, "coherence": 0, "empty": 0, "interest_cap": 0},
        "shipped": {}, "pending_audit": [], "items_by_card": {}, "attempt": 0,
    })
    try:
        await workflow_executor.execute(definition, ctx,
                                        recorder=workflow_store if workflow_run_id else None)
    finally:
        if ctx.data.get("triage_state") and not ctx.data.get("vision_resumed"):
            await _vision(pause=False)
    return out


# ---- async run trigger + status (the scheduler triggers, then polls run_status to completion) ----

_inflight = False  # in-process guard against overlapping runs


def _is_running() -> bool:
    """A run is in flight only if the store says 'running' AND it isn't stale (get_run_status applies
    the stale override) AND this process still has the task flag set."""
    return _inflight and store.get_run_status().get("state") == "running"


async def _runner(fn, req, run_id, kind):
    """Run `fn(req)` in the background, recording terminal status. Never raises."""
    global _inflight
    started = int(time.time())
    tracked = kind == "run"
    try:
        workflow_definition = workflow_store.start_run("pulse", run_id)["definition"] if tracked else None
        out = await fn(req, workflow_definition=workflow_definition, workflow_run_id=run_id) if tracked and fn is _do_run else await fn(req)
        if kind == "run_all":
            injected = [t for u in out.get("users", []) for t in (u.get("injected") or [])]
            topics, errors = [], [e for u in out.get("users", []) for e in (u.get("errors") or [])]
            gates = {}
            for u in out.get("users", []):
                for k, v in (u.get("gates") or {}).items():
                    gates[k] = gates.get(k, 0) + v
        else:
            injected, topics, errors = out.get("injected", []), out.get("topics", []), out.get("errors", [])
            gates = out.get("gates", {})
        store.set_run_status({"run_id": run_id, "state": "ok", "kind": kind, "started_at": started,
                              "finished_at": int(time.time()), "topics": topics,
                              "injected": injected, "errors": errors, "gates": gates,
                              "rounds": out.get("rounds", []) if kind != "run_all" else [],
                              "items": out.get("items", []) if kind != "run_all" else []})
        if tracked:
            workflow_store.finish_run(run_id, "ok", out)
    except Exception as e:
        store.set_run_status({"run_id": run_id, "state": "error", "kind": kind, "started_at": started,
                              "finished_at": int(time.time()), "topics": [], "injected": [],
                              "errors": [str(e)]})
        if tracked:
            workflow_store.finish_run(run_id, "error", error=str(e))
    finally:
        _inflight = False


def _start(fn, req, kind):
    """Shared trigger: refuse if a run is already in flight, else launch the background task."""
    global _inflight
    if _is_running():
        cur = store.get_run_status()
        return {"ok": True, "already_running": True, "run_id": cur.get("run_id"), "state": "running"}
    run_id = str(int(time.time()))
    _inflight = True
    store.set_run_status({"run_id": run_id, "state": "running", "kind": kind, "started_at": int(time.time()),
                          "finished_at": None, "topics": [], "injected": [], "errors": []})
    asyncio.create_task(_runner(fn, req, run_id, kind))
    return {"ok": True, "run_id": run_id, "state": "running"}


@router.post("/pulse/run", tags=["pulse"])
async def run(req: PulseRequest):
    """Trigger a Pulse run. A sweep-only call runs inline (fast); a real run returns 202
    immediately and executes in the background — poll GET /pulse/run_status for the outcome."""
    if req.sweep_only:
        return await _do_run(req)
    return _start(_do_run, req, "run")


@router.get("/pulse/run_status", tags=["pulse"])
async def run_status():
    """The current/last run state — {run_id, state(idle|running|ok|error|stale), kind,
    started_at, finished_at, topics, injected, errors, gates}. Schedulers poll this to completion."""
    return store.get_run_status()


async def run_outcome(trigger, poll_secs=10, timeout=2700):
    """Follow a /pulse/run 202 trigger to the run's terminal record and distill the outcome
    a scheduler should keep: state, shipped cards, starvation warnings, per-gate kill counts.
    Inline results (sweep_only) pass through untouched; a run still going when the wait
    expires reports itself as such rather than blocking the job slot forever."""
    if trigger.get("state") != "running":
        return trigger
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(poll_secs)
        st = store.get_run_status()
        if st.get("state") == "running":
            continue
        errors = st.get("errors") or []
        return {"state": st.get("state"),
                "warnings": [e for e in errors if e.startswith(("starved run", "under floor"))],
                "gates": st.get("gates") or {},
                "injected": st.get("injected") or []}
    return {"state": "running", "warnings": [f"run still going after {timeout}s — see /pulse/run_status"]}


class RunAllRequest(BaseModel):
    max_cards: int | None = None  # explicit cap; defaults to PULSE_MAX_CARDS and never exceeds it


async def _do_run_all(req: RunAllRequest):
    """Produce each person's OWN briefing: one per-user Pulse run grounded in their profile.
    Returns the full result dict; the endpoint runs this in the background."""
    users = await _active_users()
    if not users:
        users = [{"id": store.DEFAULT_USER, "name": None}]
    out = {"ok": True, "users": []}
    definition = workflow_store.active("pulse")["definition"]
    for u in users:
        r = await _do_run(PulseRequest(user_id=u["id"], user_name=u.get("name"),
                                       max_cards=req.max_cards, sweep_only=False),
                          workflow_definition=definition)
        out["users"].append({"user_id": u["id"], "name": u.get("name"),
                             "injected": r.get("injected", []), "errors": r.get("errors", []),
                             "gates": r.get("gates", {}), "rounds": r.get("rounds", [])})
    return out


@router.post("/pulse/run_all", tags=["pulse"])
async def run_all(req: RunAllRequest):
    """The multi-user front door for the morning briefing. Returns 202 immediately
    and runs every person's briefing in the background — point the nightly schedule here."""
    return _start(_do_run_all, req, "run_all")


# ---- Pulse feed + lifecycle endpoints (standalone store, no OWUI folder) ----


class BookmarkBody(BaseModel):
    on: bool = True


class StatusCard(BaseModel):
    kind: str = "status"
    severity: str | None = None  # "notice" | "alert" | "critical" (null = neutral)
    category: str | None = None  # System-vein sub-group — vera | infra | health | update
    title: str
    summary: str = ""
    body: str = ""


class ReadBody(BaseModel):
    card_id: str
    user_id: str | None = None


@router.get("/pulse/cards", tags=["pulse"])
async def cards(user_id: str | None = None):
    """The feed for one person. Defaults to the household owner so existing callers that
    don't pass user_id keep seeing that feed; the app passes the signed-in user's id. Each card is
    annotated with `read` for this person so the vein overlay shows per-row state."""
    uid = user_id or store.DEFAULT_USER
    cards = store.list_cards(user_id=uid)
    read = store.read_ids(uid)
    for c in cards:
        c["read"] = c["id"] in read
    return {"cards": cards}


@router.get("/pulse/veins", tags=["pulse"])
async def veins(user_id: str | None = None):
    """The pinned ambient-vein catalog, each merged with this person's UNREAD count +
    max unread severity so the chip dot/count reflects what they haven't read."""
    uid = user_id or store.DEFAULT_USER
    counts = store.unread_counts(uid)
    out = []
    for vein in pulse_veins.veins():
        cnt = counts.get(vein["kind"], {})
        merged = {**vein, "unread": cnt.get("unread", 0), "max_severity": cnt.get("max_severity")}
        # The Weather chip's calm state shows live current conditions, not a static word.
        # Lazy import avoids a circular load (weather imports from pulse). N/A if the feed is down.
        if vein["kind"] == "weather":
            from . import weather
            merged["nominal_label"] = (await weather.current_label()) or "N/A"
        out.append(merged)
    return {"veins": out}


@router.post("/pulse/read", tags=["pulse"])
async def read(b: ReadBody):
    """Record that this person opened a card's detail. Idempotent. Fired only on detail
    open (not on a vein-list glance), so the chip's unread count reflects real reads."""
    store.mark_read(b.user_id or store.DEFAULT_USER, b.card_id)
    return {"ok": True}


@router.post("/pulse/status", tags=["pulse"])
async def status_card(s: StatusCard):
    """Producer front door for ambient/status cards: run-summaries and weather/health
    alerts. Action-less — cards that carry an action use /actions/propose_card instead."""
    await _inject(s.title, s.body, summary=s.summary, kind=s.kind, severity=s.severity, category=s.category)
    return {"ok": True}


@router.post("/pulse/{card_id}/promote", tags=["pulse"])
async def promote(card_id: str):
    c = store.get_card(card_id)
    if not c:
        return {"ok": False, "error": "not found"}
    if c["status"] != "promoted":
        store.set_status(card_id, "promoted")
    return {"ok": True, "chat_id": c.get("promoted_chat_id")}


@router.post("/pulse/{card_id}/bookmark", tags=["pulse"])
async def bookmark(card_id: str, body: BookmarkBody):
    c = store.get_card(card_id)
    if not c:
        return {"ok": False, "error": "not found"}
    if body.on:
        store.set_status(card_id, "bookmarked")
    elif c["status"] == "bookmarked":
        store.set_status(card_id, "seen")
    return {"ok": True, "chat_id": c.get("promoted_chat_id")}


@router.delete("/pulse/{card_id}", tags=["pulse"])
async def remove(card_id: str):
    store.delete_card(card_id)
    return {"ok": True}
