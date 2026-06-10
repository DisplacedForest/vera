"""Overseerr media capability — search, availability, discover, request.

Vera's window into the household media stack. Overseerr (URL from config) is the front door:
it sits on top of radarr/sonarr/lidarr/prowlarr/plex and orchestrates search -> request ->
download -> Plex. We talk ONLY to its REST API; it handles the rest. With an admin API key,
requests auto-approve and start downloading immediately — explicit chat commands actuate on
the spot (the standing actuation policy).

The weekly curation digest builds on /overseerr/discover (candidate pool) +
/overseerr/search availability (dedupe) + /overseerr/request (the approve action).
"""
import aiohttp
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()

TMDB_IMG = "https://image.tmdb.org/t/p/w185"  # poster thumbnail base


def _cfg() -> dict:
    """Overseerr url/api_key from the integration registry (call time; 503 when off)."""
    from . import integrations
    cfg = integrations.integration("overseerr")
    if not cfg:
        raise HTTPException(status_code=503, detail=integrations.disabled_detail("overseerr"))
    return cfg

# Overseerr mediaInfo.status -> availability label.
# 1 unknown/none · 2 pending · 3 processing · 4 partially available · 5 available
_STATUS = {1: "not_requested", 2: "requested", 3: "processing", 4: "partially_available", 5: "available"}

# Availability values that mean "already owned or in flight" — the digest skips these.
HELD = {"requested", "processing", "partially_available", "available"}


def _hdr(cfg: dict) -> dict:
    return {"X-Api-Key": cfg.get("api_key", "")}


def _availability(info: dict | None) -> str:
    """mediaInfo (absent when the title isn't tracked) -> availability label."""
    if not info:
        return "not_requested"
    return _STATUS.get(info.get("status"), "not_requested")


def _year(date: str | None) -> int | None:
    if date and len(date) >= 4 and date[:4].isdigit():
        return int(date[:4])
    return None


def _normalize(r: dict) -> dict | None:
    """One Overseerr search/discover result -> a compact card. None for non movie/tv
    (search/trending also return `person`, which we drop)."""
    mt = r.get("mediaType")
    if mt not in ("movie", "tv"):
        return None
    poster = r.get("posterPath")
    return {
        "id": r.get("id"),  # tmdbId — what /request needs
        "media_type": mt,
        "title": r.get("title") or r.get("name"),
        "year": _year(r.get("releaseDate") or r.get("firstAirDate")),
        "overview": r.get("overview") or "",
        "availability": _availability(r.get("mediaInfo")),
        "poster": f"{TMDB_IMG}{poster}" if poster else None,
    }


async def detail_link(media_type: str, tmdb_id: int) -> str:
    """Best external link for a title: IMDb when available, else the TMDB page. One detail call."""
    tmdb_page = f"https://www.themoviedb.org/{media_type}/{tmdb_id}"
    if media_type not in ("movie", "tv"):
        return tmdb_page
    try:
        d = await _get(f"/api/v1/{media_type}/{tmdb_id}")
    except Exception:
        return tmdb_page
    imdb = d.get("imdbId") or (d.get("externalIds") or {}).get("imdbId")
    return f"https://www.imdb.com/title/{imdb}/" if imdb else tmdb_page


async def _get(path: str, params: dict | None = None) -> dict:
    cfg = _cfg()
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{cfg['url']}{path}", headers=_hdr(cfg), params=params,
                             timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status != 200:
                    raise HTTPException(status_code=502, detail=f"Overseerr {path} -> {r.status}")
                return await r.json()
    except aiohttp.ClientError as e:
        raise HTTPException(status_code=502, detail=f"Overseerr unreachable: {e}")


async def _post(path: str, body: dict) -> dict:
    cfg = _cfg()
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{cfg['url']}{path}", headers=_hdr(cfg), json=body,
                              timeout=aiohttp.ClientTimeout(total=20)) as r:
                data = await r.json()
                if r.status not in (200, 201):
                    raise HTTPException(status_code=502, detail=f"Overseerr {path} -> {r.status}: {data}")
                return data
    except aiohttp.ClientError as e:
        raise HTTPException(status_code=502, detail=f"Overseerr unreachable: {e}")


@router.get("/overseerr/search", tags=["overseerr"])
async def search(q: str = Query(..., min_length=1), limit: int = 8):
    """Search titles; each result carries its library availability (the dedupe signal)."""
    data = await _get("/api/v1/search", {"query": q, "page": 1})
    results = [n for r in (data.get("results") or []) if (n := _normalize(r))]
    return {"ok": True, "query": q, "results": results[:limit]}


@router.get("/overseerr/discover", tags=["overseerr"])
async def discover(type: str = "trending", limit: int = 20):
    """Candidate pool: trending (mixed) / movies (popular) / tv (popular). TMDB-proxied."""
    path = {
        "trending": "/api/v1/discover/trending",
        "movies": "/api/v1/discover/movies",
        "tv": "/api/v1/discover/tv",
    }.get(type)
    if not path:
        raise HTTPException(status_code=400, detail="type must be trending|movies|tv")
    data = await _get(path, {"page": 1})
    results = [n for r in (data.get("results") or []) if (n := _normalize(r))]
    return {"ok": True, "type": type, "results": results[:limit]}


class RequestBody(BaseModel):
    media_type: str                          # "movie" | "tv"
    media_id: int                            # tmdbId (from search/discover `id`)
    seasons: list[int] | str | None = None   # tv only: list of seasons or "all" (default)
    is4k: bool = False                       # HD by default


async def submit_request(media_type: str, media_id: int, seasons=None, is4k: bool = False) -> dict:
    """Core request path, callable in-process (the action executor uses this). Admin key
    auto-approves, so the request starts downloading right away."""
    if media_type not in ("movie", "tv"):
        raise HTTPException(status_code=400, detail="media_type must be movie or tv")
    body: dict = {"mediaType": media_type, "mediaId": int(media_id), "is4k": is4k}
    if media_type == "tv":
        body["seasons"] = seasons if seasons is not None else "all"
    data = await _post("/api/v1/request", body)
    media = data.get("media") or {}
    return {
        "ok": True,
        "request_id": data.get("id"),
        "media_type": media_type,
        "media_id": int(media_id),
        "availability": _STATUS.get(media.get("status"), "requested"),
    }


@router.post("/overseerr/request", tags=["overseerr"])
async def request_media(req: RequestBody):
    """Submit a request. Admin key auto-approves, so it starts downloading right away."""
    return await submit_request(req.media_type, req.media_id, req.seasons, req.is4k)


@router.get("/overseerr/requests", tags=["overseerr"])
async def requests(take: int = 20):
    """Recent requests + their status — for reporting / the curation audit."""
    data = await _get("/api/v1/request", {"take": take, "skip": 0, "sort": "added"})
    out = []
    for r in (data.get("results") or []):
        m = r.get("media") or {}
        out.append({
            "request_id": r.get("id"),
            "media_type": m.get("mediaType"),
            "tmdb_id": m.get("tmdbId"),
            "availability": _STATUS.get(m.get("status"), "not_requested"),
        })
    return {"ok": True, "results": out}
