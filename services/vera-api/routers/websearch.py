import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import aiohttp

router = APIRouter()

PLAYWRIGHT_WS = os.environ.get("PLAYWRIGHT_WS", "")  # optional: without it, results are snippets only


def _searxng_url() -> str:
    """SearXNG endpoint from the integration registry, at call time (runtime-toggleable)."""
    from . import integrations
    return (integrations.integration("searxng") or {}).get("url", "")


class SearchRequest(BaseModel):
    query: str
    max_results: int = 5
    fetch_pages: int = 3
    chars_per_page: int = 2500
    language: str = "en"


class SearchResult(BaseModel):
    title: str
    url: str
    content: str
    rendered: bool
    published: str | None = None  # source publish date (YYYY-MM-DD) when the engine provides one


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]


@router.post("/search", response_model=SearchResponse, tags=["search"])
async def search(req: SearchRequest) -> SearchResponse:
    searxng = _searxng_url()
    if not searxng:
        from . import integrations
        raise HTTPException(status_code=503, detail=integrations.disabled_detail("searxng"))
    # 1) SearXNG ranking
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                searxng,
                params={"q": req.query, "format": "json", "language": req.language},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                data = await r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SearXNG unreachable: {e}")

    raw = (data.get("results") or [])[: req.max_results]
    results = [
        {
            "title": x.get("title", "(no title)"),
            "url": x.get("url", ""),
            "content": (x.get("content") or "").strip(),
            "rendered": False,
            "published": (x.get("publishedDate") or "")[:10] or None,
        }
        for x in raw
    ]

    # 2) Playwright full-page render (best-effort) for the top fetch_pages
    if not PLAYWRIGHT_WS:
        return SearchResponse(query=req.query, results=[SearchResult(**r) for r in results])
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.connect(PLAYWRIGHT_WS)
            for res in results[: req.fetch_pages]:
                try:
                    page = await browser.new_page()
                    await page.goto(
                        res["url"], timeout=15000, wait_until="domcontentloaded"
                    )
                    body = await page.inner_text("body")
                    res["content"] = " ".join(body.split())[: req.chars_per_page]
                    res["rendered"] = True
                    await page.close()
                except Exception:
                    pass  # keep the SearXNG snippet for this one
            await browser.close()
    except Exception:
        pass  # playwright unavailable -> snippets only

    return SearchResponse(query=req.query, results=[SearchResult(**r) for r in results])
