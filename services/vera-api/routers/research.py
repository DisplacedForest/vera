"""Deep Research — agentic multi-source cited reports.

POST /research {query} runs a bounded loop:
  plan sub-questions (Vera) -> gather (web_search + Playwright pages + local RAG)
  -> synthesize a cited report (Vera, grounded only in gathered sources).

Parity with ChatGPT Deep Research / Claude Research, fully local. Reuses the web_search
router (SearXNG + Playwright) and the OWUI knowledge collections (local RAG).
"""
import json
import time

import aiohttp
from fastapi import APIRouter
from pydantic import BaseModel

from .pulse import OWUI_BASE, OWUI_KEY, _headers, _vera
from .persona import owner, voiced
from .websearch import SearchRequest, search as web_search

router = APIRouter()

MAX_SOURCES = 14
PER_SOURCE_CHARS = 1500


class ResearchRequest(BaseModel):
    query: str
    subquestions: int = 4
    pages_per_q: int = 2
    use_rag: bool = True


def _parse_list(txt: str) -> list[str]:
    """Pull a JSON array of strings out of the planner's reply (tolerant of prose around it)."""
    try:
        arr = json.loads(txt[txt.index("["): txt.rindex("]") + 1])
        return [str(x).strip() for x in arr if str(x).strip()]
    except Exception:
        # fallback: bullet/numbered lines
        return [l.strip("-*0123456789. ").strip() for l in txt.splitlines() if len(l.strip()) > 8][:5]


async def _rag_sources(query: str) -> list[dict]:
    """Query Vera's local knowledge collections for relevant chunks (best-effort, offline)."""
    out: list[dict] = []
    if not OWUI_KEY:
        return out
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{OWUI_BASE}/api/v1/knowledge/", headers=_headers(),
                             timeout=aiohttp.ClientTimeout(total=20)) as r:
                cols = await r.json()
            items = cols.get("items", cols) if isinstance(cols, dict) else cols
            for c in (items or []):
                cid, cname = c.get("id"), c.get("name", "knowledge")
                async with s.post(f"{OWUI_BASE}/api/v1/retrieval/query/collection", headers=_headers(),
                                  json={"collection_names": [cid], "query": query, "k": 3},
                                  timeout=aiohttp.ClientTimeout(total=30)) as r:
                    q = await r.json()
                docs = q.get("documents") if isinstance(q, dict) else None
                flat = docs[0] if (docs and isinstance(docs[0], list)) else (docs or [])
                for chunk in flat[:3]:
                    if chunk:
                        out.append({"title": f"{cname} (local knowledge)", "url": "local", "content": chunk})
    except Exception:
        pass
    return out


@router.post("/research", tags=["research"])
async def research(req: ResearchRequest):
    t0 = time.time()
    out = {"ok": True, "query": req.query, "subquestions": [], "report": "", "sources": [], "errors": []}

    # 1. Plan — decompose into focused sub-questions.
    try:
        plan_sys = ("You are a research planner. Given a question, output 3-5 focused sub-questions "
                    "that, researched and combined, would thoroughly answer it. Output ONLY a JSON "
                    "array of strings, nothing else.")
        plan = await _vera([{"role": "system", "content": plan_sys}, {"role": "user", "content": req.query}],
                           temperature=0.3, think="on")
        subqs = _parse_list(plan)[: req.subquestions] or [req.query]
    except Exception as e:
        out["errors"].append(f"plan: {e}"); subqs = [req.query]
    out["subquestions"] = subqs

    # 2. Gather — web per sub-question (+ Playwright pages), then local RAG. Dedup by URL.
    sources: list[dict] = []
    seen = set()
    for sq in subqs:
        try:
            resp = await web_search(SearchRequest(query=sq, max_results=4, fetch_pages=req.pages_per_q, chars_per_page=2000))
            for r in resp.results:
                if r.url and r.url not in seen and r.content:
                    seen.add(r.url)
                    sources.append({"title": r.title, "url": r.url, "content": r.content})
        except Exception as e:
            out["errors"].append(f"search '{sq[:40]}': {e}")
    if req.use_rag:
        sources.extend(await _rag_sources(req.query))

    sources = sources[:MAX_SOURCES]
    for i, s in enumerate(sources, 1):
        s["n"] = i
    if not sources:
        out["errors"].append("no sources gathered"); out["seconds"] = round(time.time() - t0, 1)
        return out

    # 3. Synthesize — cited report grounded ONLY in the gathered sources.
    src_block = "\n\n".join(f"[{s['n']}] {s['title']} ({s['url']})\n{s['content'][:PER_SOURCE_CHARS]}" for s in sources)
    syn_sys = (f"Write a research report for {owner()}. Synthesize an answer using ONLY the "
               "numbered sources below. Put an inline citation [n] after every claim, matching the source "
               "numbers. Structure: a 2-3 sentence summary, then findings (with [n] citations), then a "
               "'Sources' list mapping each [n] to its title and URL. If sources disagree, say so. Never "
               "state a fact that isn't supported by a source. GitHub-flavored markdown, no preamble.")
    syn_usr = f"Question: {req.query}\n\nSub-questions researched:\n- " + "\n- ".join(subqs) + f"\n\nSources:\n{src_block}"
    try:
        out["report"] = (await _vera([{"role": "system", "content": voiced(syn_sys)}, {"role": "user", "content": syn_usr}],
                                     temperature=0.4, think="on")).strip()
    except Exception as e:
        out["errors"].append(f"synthesis: {e}")
    out["sources"] = [{"n": s["n"], "title": s["title"], "url": s["url"]} for s in sources]
    out["seconds"] = round(time.time() - t0, 1)
    return out
