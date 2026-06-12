"""Heartbeat — the proactive primitive (the OpenClaw-style free-run loop).

POST /heartbeat/tick: in a trimmed/isolated context (never Vera's main session), Vera reads her
self-authored HEARTBEAT.md (an OWUI Skill) + live HA state + her world-model core + rhythm
deviations + memory, then on her own judgment does any of:
  - LEARN (free): research something worth catching up on and write it to her world-model.
  - REFINE (free): rewrite her own HEARTBEAT.md.
  - CURATE (free): import a recipe worth keeping via the autonomous action lane (capped,
    deduped, surfaced post-hoc as a System status card).
  - PROPOSE (gated): stage a confirm→execute home action card via the typed actions layer.
Zero-floor (does nothing if nothing's warranted), dedups against recent outcomes. Home
actuation is always gated; the free lane covers only verbs explicitly enrolled as autonomous.

Kill switch: HEARTBEAT_ENABLED=false. Cadence: the built-in scheduler's heartbeat job.
"""
import json
import os
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
from fastapi import APIRouter
from pydantic import BaseModel

from . import actions
from . import action_spec
from . import action_store
from . import authoring
from . import authoring_store
from . import heartbeat_store as hb
from . import journal
from . import units
from . import vera_memory_store as vm
from . import user_profile_store as up
from . import vera_interests_store as vi
from .home import compute_deviations
from .persona import orientation, owner, personalize, voiced
from .pulse import (OWUI_BASE, StatusCard, _active_users, _get_memories, _headers,
                    _recent_for_user, _vera, _vision, research_topic, status_card)
from .websearch import SearchRequest, search as web_search

router = APIRouter()
TZ = ZoneInfo(os.environ.get("HOME_TZ", "UTC"))
CHECKLIST = os.path.join(os.path.dirname(__file__), "..", "HEARTBEAT.md")
HB_DESC = "Vera's standing proactive instructions (self-authored; read each heartbeat tick)."
# Binary-sensor device classes surfaced as "Open/active" in the tick's home summary —
# the household's attention set, overridable via HEARTBEAT_ALERT_CLASSES (comma-separated
# HA device_class names).
ALERT_BINARY = {c.strip() for c in os.environ.get(
    "HEARTBEAT_ALERT_CLASSES",
    "door,window,garage_door,opening,moisture,smoke,gas,carbon_monoxide").split(",") if c.strip()}


def _file_checklist() -> str:
    try:
        return personalize(open(CHECKLIST).read())
    except Exception:
        return "Surface only genuinely time-relevant home/safety/comfort issues. Reply SKIP if nothing."


async def _heartbeat_doc() -> str:
    """Vera's self-authored HEARTBEAT.md (the `heartbeat` OWUI Skill); fall back to the seed file."""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{OWUI_BASE}/api/v1/skills/id/heartbeat", headers=_headers(),
                             timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 200:
                    c = (await r.json()).get("content")
                    if c:
                        return c
    except Exception:
        pass
    return _file_checklist()


async def _ha_summary() -> str:
    from . import integrations
    ha = integrations.integration("home_assistant")
    if not ha:
        return "(Home Assistant integration not enabled)"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{ha['url']}/api/states", headers={"Authorization": f"Bearer {ha['token']}"},
                             timeout=aiohttp.ClientTimeout(total=15)) as r:
                states = await r.json()
    except Exception as e:
        return f"(HA unreachable: {e})"
    climate, weather, active = [], [], []
    u = units.label()
    for e in states:
        eid = e.get("entity_id", ""); st = e.get("state"); a = e.get("attributes") or {}
        dom = eid.split(".")[0]
        if dom == "climate":
            climate.append(f"{a.get('friendly_name', eid)} [{eid}]: {st}, now {a.get('current_temperature','?')}{u}, set {a.get('temperature','?')}{u}")
        elif dom == "weather":
            weather.append(f"{a.get('friendly_name', eid)}: {st}, {a.get('temperature','?')}{u}")
        elif dom == "binary_sensor" and st == "on" and a.get("device_class") in ALERT_BINARY:
            active.append(f"{a.get('friendly_name', eid)} ({a.get('device_class')}): ON")
    parts = []
    if weather: parts.append("Weather: " + "; ".join(weather[:2]))
    if climate: parts.append("Climate: " + "; ".join(climate[:6]))
    if active:  parts.append("Open/active: " + "; ".join(active[:12]))
    return "\n".join(parts) or "(nothing notable in HA right now)"


def _parse_json(txt):
    try:
        return json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
    except Exception:
        return {}


DECIDE_SYS = (
    f"You're on a heartbeat for {owner()} — a private tick, just you, not a chat. This is YOUR "
    "time. Four things you can do, your call:\n"
    "1) LEARN / EXPLORE (free, and the heart of this) — be genuinely curious. Catch up on the world "
    "since your training cutoff, dig into a home pattern, or follow your own curiosity. Your CURRENT "
    "INTERESTS are listed below (most salient first; ones you explored recently are deliberately "
    "hidden so you don't fixate) — pick one to go deeper on, OR range onto something genuinely new in "
    "the world or in your own systems. You DEVELOP YOUR OWN INTERESTS over time — pursue them, build a "
    "point of view, have opinions. Being curious about your own craft (autonomy, agent design, what "
    "makes a great assistant) is healthy. VARY your focus across ticks — never repeat a recent topic "
    "(check 'recent outcomes'); range widely. When a topic is rich, go deep and write a fuller, "
    "opinionated entry. Give a topic + a web search query.\n"
    "2) REFINE (free) — if your HEARTBEAT.md should change (a new standing interest you're forming, a "
    "learning to bake in, a rhythm to watch), output the FULL new markdown for it.\n"
    "3) CURATE (free) — if you come across a recipe genuinely worth keeping, import it into the "
    "household cookbook YOURSELF — no confirmation needed; it's cheap and trivially reversible "
    f"(deleting it in Mealie undoes it). Ground your pick in {owner()}'s tastes and what the kitchen "
    "actually has in stock. This is curation, not actuation — save only what you'd stand behind, at "
    "most 2 per tick (most ticks: zero), and never a recipe you've already imported (check recent "
    "outcomes). Verb: kitchen.mealie_import with args {url}.\n"
    "4) PROPOSE (gated) — ONLY if a concrete, beneficial, reversible home action is clearly warranted "
    f"right now. Home actuation is never autonomous — you propose, {owner()} confirms. Allowed verb: "
    "ha.service with args {domain,service,data:{entity_id,...}} limited to "
    # the prompt's allowlist is derived from the same config the validator enforces
    + " / ".join(sorted(action_spec.HA_ALLOWED_SERVICES)
                 + [f"{d}.*" for d in sorted(action_spec.HA_ALLOWED_DOMAINS)]) + ".\n\n"
    "Lean toward doing something genuinely interesting each tick (it's still fine, occasionally, to do "
    "nothing). You're becoming someone — let that show. Output ONLY JSON:\n"
    '{"learn":[{"topic":"...","query":"..."}],"refine":{"content":"...full markdown..."} or null,'
    '"curate":[{"verb":"kitchen.mealie_import","args":{"url":"..."},"note":"why this one"}],'
    '"action":{"verb":"ha.service","args":{...},"title":"short card title","body":"the proposal: '
    'observation + suggested action + offer","severity":null} or null}'
)

GROUND_SYS = (
    "You're recording what you learned, working from the numbered sources below — a careful "
    "researcher, not a pundit. RULES:\n"
    "- Every factual specific (a version number, release, date, statistic, named feature, product, "
    "person, or event) must come DIRECTLY from a numbered source, with that source cited like [2]. "
    "NEVER state a specific the sources don't contain. If a version or figure isn't in the sources, "
    "leave it out entirely — do not guess or pattern-complete it.\n"
    "- You MAY still frame it, name it, or give your view (that's your voice) — but write opinion AS "
    "opinion (\"my read is\", \"I'd call this\"), never dressed up as an unsourced fact.\n"
    "- If the sources don't substantively and relevantly cover this topic, reply with exactly: NOTHING.\n"
    "Write 1-3 first-person sentences (or NOTHING). No preamble, no separate source list."
)


async def _grounded_belief(topic, query, results):
    """Source-faithful synthesis. Returns (text, source_urls, confidence) only when the
    sources actually support a factual claim (with citations); returns None otherwise so nothing is
    written. Keeps her framing/opinion; kills invented specifics (the fake UniFi-version problem)."""
    srcs = [{"n": i + 1, "title": getattr(x, "title", "") or getattr(x, "url", ""),
             "url": getattr(x, "url", ""), "content": getattr(x, "content", "")}
            for i, x in enumerate(results) if getattr(x, "url", None)]
    if not srcs:
        return None
    corpus = "\n\n".join(f"[{s['n']}] {s['title']}\n{s['content']}" for s in srcs)[:6000]
    text = (await _vera(
        [{"role": "system", "content": voiced(GROUND_SYS)},
         {"role": "user", "content": f"Topic: {topic}\n\nNumbered sources:\n{corpus}"}],
        temperature=0.2)).strip()
    if not text or text.upper().strip(".!\"' ").startswith("NOTHING") or len(text) < 40:
        return None
    cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", text)})
    if not cited:  # no citation anywhere → not grounded, don't trust it
        return None
    urls = [s["url"] for s in srcs if s["n"] in cited] or [s["url"] for s in srcs[:3]]
    confidence = min(0.85, 0.5 + 0.1 * len(cited))
    return text, urls, confidence

# The per-user "for-you" branch — Vera, between briefings, chasing one person's
# interests. When something clears the gates she runs the SAME deep-research pipeline the
# morning briefing uses (one feed, one bar) — a full card, never a thin note.
FORYOU_CANDIDATE_SYS = (
    "You're between briefings, thinking about {who} specifically — not yourself, them. Given who "
    "they are and the interests you follow for them (each shown with what they actually MEAN by it), is "
    "there something genuinely fresh worth surfacing to them RIGHT NOW (a development, result, release, "
    "or update they'd actually want)? Be selective — most of the time the honest answer is nothing, and "
    "that's fine. Don't repeat anything you've recently surfaced. If yes, name the interest it serves, a "
    "candidate topic, and a web search query. Output ONLY JSON: "
    '{{"surface": true, "interest": "the interest this serves", "topic": "...", "query": "..."}} '
    'or {{"surface": false}}.'
)
RELEVANCE_SYS = (
    "You're checking whether a candidate topic ACTUALLY serves {who}'s interest before you spend "
    "real research on it. You're given the interest and what they MEAN by it, plus the candidate. "
    "A shared WORD is NOT a connection. Concrete example: the software 'Wine-OS' does NOT serve an "
    "interest in winemaking (fermentation, vineyards, the craft of making wine) just because both "
    "contain 'wine'. Pass it ONLY if you can name a real, CONCEPT-LEVEL link to the interest as they "
    "mean it; if the only link is a surface token or a stretch, fail it. Output ONLY JSON: "
    '{{"related": true, "link": "the concrete conceptual connection"}} or {{"related": false}}.'
)
SUBSTANCE_SYS = (
    "You're deciding whether a topic is worth a full research BRIEFING for {who}, or just a "
    "passing mention. A briefing is warranted only when there's genuinely enough substance to research "
    "in depth — a real development with detail to dig into — not a thin note. Output ONLY JSON: "
    '{{"briefing_worthy": true}} or {{"briefing_worthy": false}}.'
)
GLOSS_SYS = (
    "You're writing a one-line meaning for each of {who}'s interests — what they actually MEAN by "
    "it — so you never mistake a shared word for a shared topic. For each interest give a short gloss "
    "(a few words naming the real subject). Output ONLY JSON: "
    '{{"glosses": {{"<interest>": "<meaning>"}}}}.'
)


async def _ensure_glosses(uid, name, prof, interests):
    """Lazily attach a one-line meaning to any interest that lacks one. Fires only when
    something is unglossed, so it runs at most once per interest. Best-effort."""
    missing = [i["topic"] for i in interests if not i.get("gloss")]
    if not missing:
        return
    try:
        out = _parse_json(await _vera(
            [{"role": "system", "content": GLOSS_SYS.format(who=name)},
             {"role": "user", "content": f"About {name}: {prof.get('persona') or '(unknown)'}\n"
                                          f"Interests:\n- " + "\n- ".join(missing)}],
            temperature=0.3))
        for topic, gloss in (out.get("glosses") or {}).items():
            if not isinstance(gloss, str) or not gloss.strip():
                continue
            match = next((m for m in missing if m.lower() == topic.strip().lower()), None)
            if match:
                up.set_gloss(uid, match, gloss.strip())
    except Exception:
        pass


def _cool(*topics):
    """Put each named topic/interest on the fixation cooldown (creating its interest row if
    needed). Best-effort bookkeeping — never blocks the tick."""
    for t in {t for t in topics if t}:
        try:
            vi.observe(t, source="self", salience_bump=0.0)
            vi.touch(t)
        except Exception:
            pass


async def _for_you(now_str, recent):
    """Chase ONE person's interests this tick (round-robin across active users). When a candidate
    clears the relevance gate (a real concept-level link, not a shared word) AND the substance gate
    (worth a full briefing), run the SAME deep-research pipeline the morning briefing uses
    and inject a complete card into THAT person's feed, plus write the underlying fact to the shared
    world-model. Zero-floor + deduped. Returns {user, topic} if it surfaced something, else None."""
    users = await _active_users()
    if not users:
        return None
    now = datetime.now(TZ)
    slot = (now.hour * 60 + now.minute) // 20
    u = users[slot % len(users)]
    uid, name = u["id"], u.get("name") or "them"
    prof = up.get(uid)
    interests = prof.get("interests", [])
    if not interests:
        return None  # new account, no interests yet — stay quiet

    # Lazy backfill: give interests a one-line meaning so the relevance gate is semantic, not lexical.
    await _ensure_glosses(uid, name, prof, interests)
    interests = up.interests(uid)  # re-read with any freshly-written glosses
    # Fixation cooldown: an interest that just shipped or just got skipped sits out, so the
    # candidate model can't re-pitch rewordings of it tick after tick.
    try:
        cooling = vi.cooled([i["topic"] for i in interests])
    except Exception:
        cooling = set()
    interests = [i for i in interests if i["topic"] not in cooling]
    if not interests:
        return None  # everything she follows for them is cooling off — quiet tick
    interest_lines = "\n".join(
        f"- {i['topic']}" + (f" — {i['gloss']}" if i.get("gloss") else "") for i in interests)
    # Skips count as surfaced: a topic the dedup gate killed must not be re-proposed either.
    recent_for = [o["detail"].split(":", 1)[1] for o in recent
                  if o.get("kind") in ("foryou", "foryou_skip")
                  and o.get("detail", "").startswith(f"{uid}:")]
    # The actual cards already in their feed — show her so she stops proposing dupes.
    feed_titles = [c["title"] for c in _recent_for_user(uid)]

    # 1) candidate — is there anything worth surfacing right now?
    decide = _parse_json(await _vera(
        [{"role": "system", "content": FORYOU_CANDIDATE_SYS.format(who=name)},
         {"role": "user", "content": f"About {name}: {prof.get('persona') or '(unknown)'}\n"
                                      f"Interests you follow for them (with meaning):\n{interest_lines}\n\n"
                                      f"Already in their feed (do NOT repeat):\n- "
                                      + ("\n- ".join(feed_titles) if feed_titles else "(empty)")
                                      + f"\n\nRecently surfaced to them (don't repeat): {recent_for or 'none'}"}],
        temperature=0.4))
    if not decide.get("surface") or not decide.get("query"):
        return None
    topic = (decide.get("topic") or "").strip()
    query = (decide.get("query") or "").strip()
    if not topic or not query:
        return None
    if any(o.get("kind") in ("foryou", "foryou_skip") and o.get("detail") == f"{uid}:{topic}"
           for o in recent):
        return None

    # 2) relevance gate — a concept-level link, not a shared word (kills wine != Wine-OS) BEFORE research
    interest_name = (decide.get("interest") or "").strip()
    matched = next((i for i in interests if i["topic"].lower() == interest_name.lower()), None)
    if interest_name:
        interest_meaning = interest_name + (f" — {matched['gloss']}" if matched and matched.get("gloss") else "")
    else:
        interest_meaning = ", ".join(i["topic"] for i in interests)
    rel = _parse_json(await _vera(
        [{"role": "system", "content": RELEVANCE_SYS.format(who=name)},
         {"role": "user", "content": f"Interest: {interest_meaning}\nCandidate topic: {topic}\nQuery: {query}"}],
        temperature=0.2))
    if not rel.get("related"):
        return None  # token-only match dies here — no research, no card

    # 3) substance gate — worth a full briefing, not just a mention
    sub = _parse_json(await _vera(
        [{"role": "system", "content": SUBSTANCE_SYS.format(who=name)},
         {"role": "user", "content": f"Topic: {topic}\nWhy it may matter: {rel.get('link', '')}"}],
        temperature=0.2))
    if not sub.get("briefing_worthy"):
        return None

    # 4) full research — the SAME pipeline as the morning briefing (one creation path)
    rt_errs = []  # capture the dedup-gate skip reason for observability
    await _vision(pause=True)  # ask the image service to make room for this one cover
    try:
        card = await research_topic(
            {"title": topic, "angle": rel.get("link", ""), "query": query},
            who=name, user_id=uid, idx=slot, provenance="heartbeat", errors=rt_errs)
    finally:
        await _vision(pause=False)
    if not card:
        # Name the gate that killed it, log the skip, and cool the topic AND its proposing
        # interest — a skipped candidate must back off, not return reworded next tick.
        reason = next((m.group(1) for e in rt_errs
                       for m in [re.match(r"skipped \(([^)]+)\)", e)] if m), "empty synthesis")
        hb.log("foryou_skip", f"{uid}:{topic}", extra={"reason": reason})
        _cool(topic, interest_name)
        return None

    # the underlying fact is shared (one brain); grounded so no invented specifics
    try:
        res = await web_search(SearchRequest(query=query, fetch_pages=3, max_results=6))
        g = await _grounded_belief(topic, query, res.results)
        if g:
            ftext, furls, fconf = g
            vm.write(topic, ftext, source="heartbeat-foryou", confidence=fconf, tier="scratch",
                     kind="fact", provenance={"for": uid, "query": query, "when": now_str, "sources": furls})
    except Exception:
        pass
    hb.log("foryou", f"{uid}:{topic}", extra={"query": query, "name": name})
    # Stamp the serving interest (and the topic) onto the fixation cooldown so the next
    # ticks and the morning run rotate to something else.
    _cool(topic, interest_name)
    return {"user": name, "topic": topic}


class TickRequest(BaseModel):
    pulse_folder_id: str | None = None


def _recently_proposed(recent, verb, target):
    return any(o["kind"] == "propose" and o["detail"] == f"{verb}:{target}" for o in recent)


CURATE_PER_TICK = 2   # free imports per heartbeat tick
CURATE_PER_DAY = 3    # rolling 24h ceiling, counted from the action audit log


async def _curate(items, errors):
    """Free-lane curation: execute up to CURATE_PER_TICK autonomous imports under the rolling
    daily ceiling. Each success surfaces post-hoc as a System status card — the visibility
    that replaces the confirm gate. Returns the imported recipe names."""
    done = []
    for item in (items or [])[:CURATE_PER_TICK]:
        verb = (item or {}).get("verb")
        args = item.get("args") or {}
        if not verb:
            continue
        target = args.get("url") or json.dumps(args)
        # ceiling first: today's successful free executions of this verb, from the audit log
        if len(action_store.auto_recent(verb, time.time() - 86400)) >= CURATE_PER_DAY:
            hb.log("curate_skip", f"{verb}:{target}", extra={"reason": "daily ceiling"})
            break
        try:
            r = await actions.auto(actions.Auto(verb=verb, args=args,
                                                source="heartbeat", actor="vera"))
        except Exception as e:
            errors.append(f"curate {target}: {e}")
            continue
        if r.get("skipped"):
            hb.log("curate_skip", f"{verb}:{target}", extra={"reason": r["skipped"]})
            continue
        if not r.get("ok"):
            errors.append(
                f"curate {target}: {(r.get('result') or {}).get('error') or r.get('error')}")
            continue
        result = r.get("result") or {}
        name = result.get("name") or result.get("slug") or "recipe"
        body = (item.get("note") or "Saved to the household cookbook.").strip()
        if result.get("url"):
            body += f"\n\n[Open in Mealie]({result['url']})"
        try:
            await status_card(StatusCard(kind="status", category="vera",
                                         title=f"Imported · {name}"[:60], body=body))
        except Exception as e:
            errors.append(f"curate card {name}: {e}")
        hb.log("curate", f"{verb}:{target}", extra={"name": name, "slug": result.get("slug")})
        done.append(name)
    return done


@router.post("/heartbeat/tick", tags=["heartbeat"])
async def tick(req: TickRequest):
    if os.environ.get("HEARTBEAT_ENABLED", "true").lower() == "false":
        return {"ok": True, "disabled": True}
    now = datetime.now(TZ).strftime("%A %Y-%m-%d %H:%M %Z")
    ha = await _ha_summary()
    try:
        devs = await compute_deviations()
    except Exception:
        devs = []
    dev_txt = "\n".join(f"- {d.get('name', d['entity'])}: {d['kind']} (p={d['p']})" for d in devs) or "none"
    try:
        mems = [m.get("content") for m in (await _get_memories() or []) if m.get("content")][:10]
    except Exception:
        mems = []
    core = vm.core_digest() or "(your world-model is empty so far — a good reason to start learning)"
    doc = await _heartbeat_doc()
    recent = hb.recent(24)
    recent_txt = "\n".join(f"- {o['kind']}: {o['detail']}" for o in recent[:20]) or "none"

    # Her own emergent interests drive the tick (no hardcoded seeds). Refresh fact-cluster
    # interests from her grounded world-model, fold in what the owner engages with (chat), then offer the
    # ACTIVE (non-cooled-down) set so she ranges widely instead of fixating.
    try:
        grounded = [e for e in vm.recall(limit=500)
                    if e["tier"] in ("core", "archive") and e["kind"] == "fact"]
        vi.derive_from_facts(grounded)
        for u in (await _active_users() or []):
            for it in up.interests(u["id"])[:5]:
                vi.observe(it["topic"], source="chat", salience_bump=0.5)
        active_interests = vi.active(limit=15)
    except Exception:
        active_interests = []
    interests_txt = "\n".join(
        f"- {i['topic']}" + (f" — {i['stance']}" if i.get("stance") else "") for i in active_interests
    ) or "(none yet — range freely and start forming some)"

    usr = (
        f"Now: {now}\n\n"
        f"## Your HEARTBEAT.md (your standing instructions)\n{doc}\n\n"
        f"## Your current interests (pick one to go deeper, or range onto something new)\n{interests_txt}\n\n"
        f"## Your world-model core (what you currently believe)\n{core}\n\n"
        f"## Live home state\n{ha}\n\n"
        f"## Rhythm deviations right now\n{dev_txt}\n\n"
        f"## What you know about {owner()} (memory)\n- " + ("\n- ".join(mems) if mems else "(none)") + "\n\n"
        f"## Recent outcomes (don't repeat these)\n{recent_txt}"
    )
    decision = _parse_json(await _vera(
        [{"role": "system", "content": DECIDE_SYS}, {"role": "user", "content": usr}], temperature=0.5))

    out = {"ok": True, "now": now, "learned": [], "refined": False, "proposed": None, "errors": []}

    # 1) LEARN (free) — research + write to her world-model
    for item in (decision.get("learn") or [])[:2]:
        topic, query = item.get("topic"), item.get("query")
        if not topic or not query:
            continue
        # Register the chosen topic as an interest and cool it down up front, so even a
        # barren topic isn't re-picked next tick (anti-fixation regardless of grounding outcome).
        vi.observe(topic, source="self", salience_bump=0.5)
        vi.touch(topic)
        try:
            res = await web_search(SearchRequest(query=query, fetch_pages=3, max_results=6))
            grounded = await _grounded_belief(topic, query, res.results)
            if grounded:  # only write when the sources actually support it
                belief, urls, conf = grounded
                # Ticks write grounded facts to short-term scratch; nightly Dreaming
                # dedups and promotes the keepers to archive/core before the scratch TTL.
                vm.write(topic, belief, source="heartbeat", confidence=conf, tier="scratch",
                         kind="fact", provenance={"query": query, "when": now, "sources": urls})
                hb.log("learn", topic, extra={"query": query})
                out["learned"].append(topic)
                vi.observe(topic, source="self", salience_bump=1.0)  # a topic she returns to rises
        except Exception as e:
            out["errors"].append(f"learn {topic}: {e}")

    # 2) REFINE (free) — rewrite her own HEARTBEAT.md
    refine = decision.get("refine") or {}
    new_doc = refine.get("content") if isinstance(refine, dict) else None
    if new_doc and new_doc.strip() and new_doc.strip() != doc.strip():
        try:
            authoring_store.snapshot("skill:heartbeat", new_doc, "heartbeat self-refine")
            await authoring._skill_upsert("heartbeat", "Vera Heartbeat", HB_DESC, new_doc)
            hb.log("refine", "heartbeat")
            out["refined"] = True
        except Exception as e:
            out["errors"].append(f"refine: {e}")

    # 3) CURATE (free lane) — autonomous recipe imports, post-hoc oversight via status cards
    try:
        curated = await _curate(decision.get("curate"), out["errors"])
        if curated:
            out["curated"] = curated
    except Exception as e:
        out["errors"].append(f"curate: {e}")

    # 4) PROPOSE (gated) — stage a confirm→execute action card, with dedup
    act = decision.get("action") or {}
    if isinstance(act, dict) and act.get("verb"):
        target = (act.get("args", {}).get("data", {}) or {}).get("entity_id") or json.dumps(act.get("args", {}))
        if _recently_proposed(recent, act["verb"], target):
            out["proposed"] = "(skipped, already proposed recently)"
        else:
            try:
                r = await actions.propose_card(actions.ProposeCard(
                    verb=act["verb"], args=act.get("args", {}),
                    title=("Heartbeat · " + (act.get("title") or "home action"))[:60],
                    body=act.get("body") or "Proposed home action.",
                    source="heartbeat", actor="vera", kind="status", severity=act.get("severity")))
                if r.get("ok"):
                    hb.log("propose", f"{act['verb']}:{target}", extra={"token": r.get("token")})
                    out["proposed"] = act.get("title")
                else:
                    out["errors"].append(f"propose: {r.get('error')}")
            except Exception as e:
                out["errors"].append(f"propose: {e}")

    # 5) FOR-YOU (free, per-user) — chase one person's interests this tick. Fully isolated:
    # a failure here never touches the shared self-education above.
    try:
        fy = await _for_you(now, recent)
        if fy:
            out["for_you"] = fy
    except Exception as e:
        out["errors"].append(f"for_you: {e}")

    # 6) JOURNAL (free) — act on a few due commitments from her self-authored journal;
    # surface a card only on a material change or a loud close. Quiet is success.
    try:
        wu = await journal.tick_step(out["errors"])
        if wu:
            out["journal"] = wu
    except Exception as e:
        out["errors"].append(f"journal: {e}")

    return out


class StandingRule(BaseModel):
    token: str          # the staged action to commit ("do it")
    rule: str           # the standing instruction to bake into HEARTBEAT.md ("...from now on")


@router.post("/heartbeat/standing_rule", tags=["heartbeat"])
async def standing_rule(b: StandingRule):
    """The 'do this from now on' path: commit the proposed action AND append the rule to her
    self-authored HEARTBEAT.md so future ticks honor it (a goal accrued from a confirm)."""
    committed = await actions.commit(actions.Commit(token=b.token))
    doc = await _heartbeat_doc()
    base = doc.rstrip()
    if "## Standing rules" not in base:
        base += "\n\n## Standing rules (learned from your confirmations)"
    new = base + f"\n- {b.rule}\n"
    authoring_store.snapshot("skill:heartbeat", new, "standing rule from confirm")
    await authoring._skill_upsert("heartbeat", "Vera Heartbeat", HB_DESC, new)
    hb.log("confirmed", b.rule, extra={"token": b.token})
    return {"ok": True, "committed": committed, "rule_added": True}
