"""Image search — real photos (not generated) for Pulse inline imagery.

Mirrors websearch but hits SearXNG's image category. Returns direct image URLs
plus the page they came from, so the Pulse pipeline can download + re-host them.
"""

import aiohttp
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class ImageSearchRequest(BaseModel):
    query: str
    max_results: int = 6
    language: str = "en"


class ImageHit(BaseModel):
    title: str
    img_src: str        # direct image URL
    thumbnail_src: str  # smaller preview (may be empty)
    source_url: str     # the page the image appears on


class ImageSearchResponse(BaseModel):
    query: str
    results: list[ImageHit]


@router.post("/images/search", response_model=ImageSearchResponse, tags=["images"])
async def search(req: ImageSearchRequest) -> ImageSearchResponse:
    from . import integrations
    searxng = (integrations.integration("searxng") or {}).get("url", "")
    if not searxng:
        raise HTTPException(status_code=503, detail=integrations.disabled_detail("searxng"))
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                searxng,
                params={
                    "q": req.query,
                    "format": "json",
                    "categories": "images",
                    "language": req.language,
                },
                timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                data = await r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SearXNG unreachable: {e}")

    raw = (data.get("results") or [])[: req.max_results]
    results = [
        ImageHit(
            title=x.get("title", "(no title)"),
            img_src=x.get("img_src") or x.get("thumbnail_src") or "",
            thumbnail_src=x.get("thumbnail_src") or "",
            source_url=x.get("url", ""),
        )
        for x in raw
        if (x.get("img_src") or x.get("thumbnail_src"))
    ]
    return ImageSearchResponse(query=req.query, results=results)
