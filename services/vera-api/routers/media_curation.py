"""Weekly media curation — Vera as a tasteful media librarian. EXPERIMENTAL: this pipeline
has run once at scale; treat its picks as suggestions and keep the digest gate on.

Once a week: build a candidate pool from Overseerr/TMDB discover + a web_search zeitgeist pass,
let Vera select against the household's configured taste profile (popularity is a candidate pool,
not a ranking), drop anything already in the library or previously decided on, and emit ONE
multi-item approve/skip digest card in the Media lane. Each pick is staged as an
`overseerr_request` action, so approving it downloads the title and is audited/undoable.

Taste is config, not code: MEDIA_CURATION_TASTE carries the household's hard rules and
preferences and steers both the zeitgeist title extraction and the final selection. Enabled via
the overseerr integration's media_curation feature; triggered weekly by the built-in scheduler
hitting POST /media/curate.
"""
import json
import os

from fastapi import APIRouter, HTTPException

from . import media_store as mstore
from . import overseerr
from . import websearch
from .actions import DigestItem, propose_digest_card
from .pulse import _vera

router = APIRouter()

CAP = int(os.environ.get("MEDIA_CURATION_CAP", "8"))
POOL_MAX = int(os.environ.get("MEDIA_CURATION_POOL_MAX", "60"))  # cap what we hand the model, to keep the prompt bounded
DISCOVER_LIMIT = int(os.environ.get("MEDIA_CURATION_DISCOVER_LIMIT", "20"))  # per discover type
ZEITGEIST_QUERY = os.environ.get("MEDIA_CURATION_ZEITGEIST_QUERY", "").strip() or (
    "movies and TV shows everyone is talking about right now (this week)")
ZEITGEIST_RESULTS = int(os.environ.get("MEDIA_CURATION_ZEITGEIST_RESULTS", "6"))
ZEITGEIST_PAGES = int(os.environ.get("MEDIA_CURATION_ZEITGEIST_PAGES", "3"))

_NEUTRAL_TASTE = (
    "Aim for a well-rounded, genuinely good collection. Raw popularity is polluted — treat it "
    "as a candidate pool, NOT a ranking. Choose with taste: genuine critical acclaim and real "
    "current cultural relevance over mere view counts."
)


def _taste() -> str:
    """The household's taste profile — the media lane's taste option (store >
    MEDIA_CURATION_TASTE env), else the neutral default that assumes nothing beyond
    distrusting raw popularity."""
    from . import pulse_lanes
    return (str(pulse_lanes.option_values("media").get("taste") or "").strip()
            or _NEUTRAL_TASTE)


def _json(txt: str):
    try:
        return json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
    except Exception:
        return {}


async def _discover_pool() -> list[dict]:
    pool = []
    for t in ("trending", "movies", "tv"):
        try:
            d = await overseerr.discover(type=t, limit=DISCOVER_LIMIT)
            pool += d.get("results") or []
        except Exception:
            continue
    return pool


async def _zeitgeist_pool() -> list[dict]:
    """web_search for what's culturally in the air, have Vera name titles, resolve each to a real
    Overseerr entry (grounded tmdb id + availability)."""
    try:
        resp = await websearch.search(websearch.SearchRequest(
            query=ZEITGEIST_QUERY,
            max_results=ZEITGEIST_RESULTS, fetch_pages=ZEITGEIST_PAGES))
        context = "\n\n".join(f"{r.title}\n{r.content[:800]}" for r in resp.results)
    except Exception:
        return []
    if not context.strip():
        return []
    out = await _vera([
        {"role": "system", "content": "You extract concrete movie/TV titles people are currently "
         "talking about from web snippets. " + _taste() + " Return ONLY JSON: "
         '{"titles": ["Title 1", "Title 2", ...]} (up to 12).'},
        {"role": "user", "content": context},
    ], temperature=0.3)
    titles = (_json(out).get("titles") or [])[:12]
    resolved = []
    for title in titles:
        if not isinstance(title, str) or not title.strip():
            continue
        try:
            s = await overseerr.search(q=title, limit=1)
            if s.get("results"):
                resolved.append(s["results"][0])
        except Exception:
            continue
    return resolved


def _dedupe_and_filter(pool: list[dict]) -> list[dict]:
    seen_decided = mstore.seen_keys()
    out, keys = [], set()
    for m in pool:
        mt, mid = m.get("media_type"), m.get("id")
        if mt not in ("movie", "tv") or mid is None:
            continue
        key = (mt, mid)
        if key in keys:                               # collapse cross-source duplicates
            continue
        if m.get("availability") in overseerr.HELD:   # already owned / requested / in flight
            continue
        if key in seen_decided:                       # previously skipped or approved — never resurface
            continue
        keys.add(key)
        out.append(m)
    return out


async def _select(pool: list[dict], cap: int) -> list[dict]:
    """Vera picks up to `cap` from the pool, enforcing the taste rules. Returns the chosen pool items."""
    catalog = "\n".join(
        f'{i}. {m["title"]} ({m.get("year") or "?"}) [{m["media_type"]}] — {(m.get("overview") or "")[:160]}'
        for i, m in enumerate(pool)
    )
    out = await _vera([
        {"role": "system", "content":
         "You're curating what to add to the household's media library. From the numbered "
         f"candidates, choose UP TO {cap} worth adding. " + _taste() + " Return ONLY JSON: "
         '{"picks": [{"i": <number>, "reason": "<=8 words"}]}'},
        {"role": "user", "content": catalog},
    ], temperature=0.4)
    picks = _json(out).get("picks") or []
    chosen = []
    for p in picks:
        try:
            i = int(p.get("i"))
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(pool):
            m = dict(pool[i])
            m["reason"] = (p.get("reason") or "").strip()
            chosen.append(m)
        if len(chosen) >= cap:
            break
    return chosen


@router.post("/media/curate", tags=["media"])
async def curate(force: bool = False):
    """Run the weekly curation and post the digest card. Gated behind the overseerr
    integration's experimental media_curation feature; `force` bypasses the gate for
    a deliberate manual run (the overseerr integration itself must still be enabled)."""
    from . import integrations, pulse_lanes
    if not force and not pulse_lanes.is_enabled("media"):
        raise HTTPException(status_code=503, detail=pulse_lanes.gate_reason("media"))
    if not force and not integrations.feature_enabled("overseerr", "media_curation"):
        raise HTTPException(
            status_code=503,
            detail="media curation is off — enable the overseerr integration's media_curation feature")

    pool = _dedupe_and_filter(await _discover_pool() + await _zeitgeist_pool())
    if not pool:
        return {"ok": True, "candidates": 0, "picked": 0, "note": "no eligible candidates"}

    lane_opts = pulse_lanes.option_values("media")
    cap = int(lane_opts.get("cap") or CAP)
    chosen = await _select(pool[:POOL_MAX], cap)
    if not chosen:
        return {"ok": True, "candidates": len(pool), "picked": 0, "note": "nothing met the bar"}

    items = []
    for m in chosen:
        items.append(DigestItem(
            verb="overseerr_request",
            args={"media_type": m["media_type"], "media_id": m["id"], "title": m["title"]},
            title=m["title"],
            subtitle=" · ".join(filter(None, [
                str(m["year"]) if m.get("year") else None,
                "Movie" if m["media_type"] == "movie" else "TV",
                m.get("reason") or None,
            ])),
            media_type=m["media_type"],
            tmdb_id=m["id"],
            poster=m.get("poster"),
            link=await overseerr.detail_link(m["media_type"], m["id"]),
        ))
    body = f"This week I'd add {len(items)} to the library. Tap add to grab each, or skip to pass."
    res = await propose_digest_card("Worth adding this week", body, items, kind="media")
    return {"ok": True, "candidates": len(pool), "picked": len(items), "card_id": res["card_id"]}
