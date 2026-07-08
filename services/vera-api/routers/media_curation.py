"""Weekly media curation — the Media vein's `media_candidates` and `media_digest` blocks.

Once a week: `media_candidates` builds a pool from Overseerr/TMDB discover + a web_search
zeitgeist pass and drops anything already in the library or previously decided on; the
vein's `llm_judge` gates each candidate against the household's configured taste profile
(popularity is a candidate pool, not a ranking); `media_digest` trims to the cap and emits
ONE multi-item approve/skip digest situation. Each pick is staged as an `overseerr_request`
action, so approving it downloads the title and is audited/undoable.

Taste is config, not code: the vein's taste option (env MEDIA_CURATION_TASTE) steers both
the zeitgeist title extraction and the judge bar.
"""
import json
import os
from datetime import datetime

from fastapi import APIRouter

from . import media_store as mstore
from . import overseerr
from . import vein_engine
from . import websearch
from .actions import DigestItem
from .pulse import TZ, _vera

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
    """The household's taste profile — the media vein's taste option (store >
    MEDIA_CURATION_TASTE env), else the neutral default that assumes nothing beyond
    distrusting raw popularity."""
    from . import pulse_veins
    return (str(pulse_veins.option_values("media").get("taste") or "").strip()
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


async def _zeitgeist_pool(taste: str | None = None) -> list[dict]:
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
         "talking about from web snippets. " + (taste or _taste()) + " Return ONLY JSON: "
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


def _taste_from(ctx) -> str:
    return (str((ctx.get("options") or {}).get("taste") or "").strip() or _NEUTRAL_TASTE)


async def _block_media_candidates(items, params, ctx):
    pool = _dedupe_and_filter(await _discover_pool() + await _zeitgeist_pool(_taste_from(ctx)))
    return items + [{
        "key": f"{m['media_type']}:{m['id']}",
        "title": f"{m['title']} ({m.get('year') or '?'})",
        "content": f"[{m['media_type']}] {(m.get('overview') or '')[:160]}",
        "media_type": m["media_type"], "tmdb_id": m["id"], "media_title": m["title"],
        "year": m.get("year"), "poster": m.get("poster"),
    } for m in pool[:POOL_MAX]]


async def _block_media_digest(items, params, ctx):
    if not items:
        return []
    from .actions import _build_digest_items
    cap = int((ctx.get("options") or {}).get("cap") or CAP)
    rows = []
    for m in items[:cap]:
        rows.append(DigestItem(
            verb="overseerr_request",
            args={"media_type": m["media_type"], "media_id": m["tmdb_id"],
                  "title": m["media_title"]},
            title=m["media_title"],
            subtitle=" · ".join(filter(None, [
                str(m["year"]) if m.get("year") else None,
                "Movie" if m["media_type"] == "movie" else "TV",
                (m.get("judge_reason") or "").strip() or None,
            ])),
            media_type=m["media_type"],
            tmdb_id=m["tmdb_id"],
            poster=m.get("poster"),
            link=await overseerr.detail_link(m["media_type"], m["tmdb_id"]),
        ))
    year, week, _ = datetime.now(TZ).isocalendar()
    n = len(rows)
    return [{"key": f"digest:{year}-W{week:02d}",
             "title": "Worth adding this week",
             "content": f"This week I'd add {n} to the library. "
                        "Tap add to grab each, or skip to pass.",
             "items": await _build_digest_items(rows)}]


vein_engine.register("media_candidates", _block_media_candidates)
vein_engine.register("media_digest", _block_media_digest)


