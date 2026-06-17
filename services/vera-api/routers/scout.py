"""Scout Agent — the Pulse pipeline's wide, cheap retrieval stage.

Given the Profile Graph's currently-live nodes, the Scout fans out focused multi-source
searches and returns a flat list of raw candidate findings for the Analyst to rank. Pure
retrieval: node selection is deterministic graph math, the one LLM call per node phrases a
query, and every source adapter degrades to nothing when its endpoint is unconfigured.

All I/O (the LLM, each adapter's HTTP fetch) is injected so the pipeline runs offline.
"""
import os

from . import profile_graph_store as pg


def _envf(name, default):
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return float(default)


def _envi(name, default):
    try:
        return int(float(os.environ.get(name, "") or default))
    except ValueError:
        return int(default)


ENGAGEMENT_FLOOR = _envf("SCOUT_ENGAGEMENT_FLOOR", "0.5")  # decayed engagement to count an interest live
MAX_NODES = _envi("SCOUT_MAX_NODES", "12")                 # cap on nodes scouted per run

_DORMANT = {"dormant", "resolved"}


def _is_live(node, now):
    """Whether a node earns a scout this run, by graph math alone. A dormant or resolved node
    is excluded; otherwise a node qualifies on its type's open/due condition (watch/project/
    thread) or, for any other type, on decayed engagement clearing ENGAGEMENT_FLOOR."""
    state = node.get("state")
    if state in _DORMANT:
        return False
    ntype = node.get("type")
    if ntype == "watch":
        nc = node.get("next_check")
        return state == "active" and (nc is None or nc <= now)
    if ntype == "project":
        return state in (None, "active")
    if ntype == "thread":
        return state == "open"
    return pg.engagement_now(node, now) >= ENGAGEMENT_FLOOR


def select_live_nodes(nodes=None, now=None):
    """The nodes to scout this run, ranked by decayed engagement (desc) and capped at
    MAX_NODES. Reads the whole graph when `nodes` is not injected."""
    import time
    now = int(time.time()) if now is None else now
    nodes = pg.all_nodes() if nodes is None else nodes
    live = [n for n in nodes if _is_live(n, now)]
    live.sort(key=lambda n: pg.engagement_now(n, now), reverse=True)
    return live[:MAX_NODES]


# --------------------------------------------------------------------------- query phrasing

ALLOWED_SOURCES = ["news", "reddit", "github", "papers", "weather", "local"]
DEFAULT_SOURCES = ["news", "reddit"]

PHRASE_SYS = (
    "You turn one thing the owner cares about into a single focused web-search query and pick "
    "which sources fit it. Reply ONLY with JSON: "
    '{"query": "<one search query, no quotes>", "sources": [<subset of '
    "news, reddit, github, papers, weather, local>]}. Pick sources by fit: github for software "
    "projects, papers for research topics, weather/local for a place, news/reddit for most else."
)


def _node_brief(node):
    """A compact node summary for the phrasing prompt: type, label, and its top facts."""
    facts = []
    for f in (node.get("facts") or [])[:4]:
        facts.append(f.get("text") if isinstance(f, dict) else str(f))
    line = f"type: {node.get('type')}\nlabel: {node.get('label')}"
    if facts:
        line += "\nfacts:\n- " + "\n- ".join(facts)
    return line


def _clamp(sources, configured):
    """The requested sources, in canonical order, kept only when allowed AND configured."""
    want = set(sources or [])
    return [s for s in ALLOWED_SOURCES if s in want and s in configured]


async def phrase_query(node, llm, configured):
    """One LLM call: phrase a focused query for `node` and pick fitting sources. Degrades to the
    node label and the default sources on any failure. Returns
    `{"query": str, "sources": [str]}` with sources clamped to ALLOWED ∩ configured."""
    fallback = {"query": (node.get("label") or "").strip(),
                "sources": _clamp(DEFAULT_SOURCES, configured)}
    try:
        raw = await llm([{"role": "system", "content": PHRASE_SYS},
                         {"role": "user", "content": _node_brief(node)}], temperature=0.2)
    except Exception:
        return fallback
    import json
    try:
        j = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return fallback
    query = (j.get("query") or "").strip() or fallback["query"]
    sources = _clamp(j.get("sources"), configured)
    if not sources:
        sources = _clamp(DEFAULT_SOURCES, configured)
    return {"query": query, "sources": sources}


# --------------------------------------------------------------------------- source adapters

NEWS_MAX = _envi("SCOUT_NEWS_MAX", "6")
REDDIT_MAX = _envi("SCOUT_REDDIT_MAX", "6")
GITHUB_MAX = _envi("SCOUT_GITHUB_MAX", "6")
PAPERS_MAX = _envi("SCOUT_PAPERS_MAX", "6")

REDDIT_BASE = "https://www.reddit.com"          # public default; override with REDDIT_BASE
GITHUB_API_BASE = "https://api.github.com"      # public default; override with GITHUB_API_BASE
ARXIV_BASE = "http://export.arxiv.org"          # public default; override with ARXIV_BASE

_UA = {"User-Agent": "vera-scout/1.0"}


def _candidate(finding_text, title, url, published_date, source, seed_node_id):
    return {"finding_text": (finding_text or "").strip(), "title": (title or "").strip(),
            "url": url or "", "published_date": published_date, "source": source,
            "seed_node_id": seed_node_id}


def _epoch_date(v):
    """A unix epoch (seconds) to a YYYY-MM-DD UTC date, or None when unparseable."""
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(float(v), timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OSError):
        return None


def _now_date(now):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")


async def _get_json(url, params=None):
    import aiohttp
    async with aiohttp.ClientSession() as s:
        async with s.get(url, params=params, headers=_UA,
                         timeout=aiohttp.ClientTimeout(total=20)) as r:
            r.raise_for_status()
            return await r.json(content_type=None)


async def _get_text(url, params=None):
    import aiohttp
    async with aiohttp.ClientSession() as s:
        async with s.get(url, params=params, headers=_UA,
                         timeout=aiohttp.ClientTimeout(total=20)) as r:
            r.raise_for_status()
            return await r.text()


def _searxng_enabled():
    from . import integrations
    return integrations.integration("searxng")


class _NewsAdapter:
    name = "news"

    def _searxng(self):
        return _searxng_enabled()

    def configured(self):
        return bool(self._searxng())

    async def search(self, query, node, now, *, fetch=None):
        from .websearch import SearchRequest, search as web_search
        fetch = fetch or web_search
        resp = await fetch(SearchRequest(query=query, max_results=NEWS_MAX))
        return [_candidate(r.content or r.title, r.title, r.url, r.published, self.name, node["id"])
                for r in resp.results]


class _LocalAdapter(_NewsAdapter):
    name = "local"

    def configured(self):
        return bool(self._searxng()) and bool(os.environ.get("HOME_LOCATION_NAME", "").strip())

    async def search(self, query, node, now, *, fetch=None):
        from . import persona
        return await super().search(f"{query} {persona.location()}", node, now, fetch=fetch)


class _RedditAdapter:
    name = "reddit"

    def _base(self):
        return (os.environ.get("REDDIT_BASE", REDDIT_BASE) or "").strip().rstrip("/")

    def configured(self):
        return bool(self._base())

    async def search(self, query, node, now, *, fetch=None):
        fetch = fetch or _get_json
        base = self._base()
        data = await fetch(f"{base}/search.json",
                           {"q": query, "sort": "new", "limit": REDDIT_MAX,
                            "t": "month", "raw_json": 1})
        out = []
        for ch in ((data.get("data") or {}).get("children") or [])[:REDDIT_MAX]:
            d = ch.get("data") or {}
            title = d.get("title") or ""
            url = base + (d.get("permalink") or "")
            body = (d.get("selftext") or "").strip()
            out.append(_candidate(body or title, title, url, _epoch_date(d.get("created_utc")),
                                  self.name, node["id"]))
        return out


class _GithubAdapter:
    name = "github"

    def _base(self):
        return (os.environ.get("GITHUB_API_BASE", GITHUB_API_BASE) or "").strip().rstrip("/")

    def configured(self):
        return bool(self._base())

    async def search(self, query, node, now, *, fetch=None):
        fetch = fetch or _get_json
        data = await fetch(f"{self._base()}/search/repositories",
                           {"q": query, "sort": "updated", "order": "desc", "per_page": GITHUB_MAX})
        out = []
        for it in (data.get("items") or [])[:GITHUB_MAX]:
            title = it.get("full_name") or ""
            desc = it.get("description") or ""
            pub = (it.get("pushed_at") or it.get("updated_at") or "")[:10] or None
            out.append(_candidate(desc or title, title, it.get("html_url") or "", pub,
                                  self.name, node["id"]))
        return out


class _PapersAdapter:
    name = "papers"
    _NS = {"a": "http://www.w3.org/2005/Atom"}

    def _base(self):
        return (os.environ.get("ARXIV_BASE", ARXIV_BASE) or "").strip().rstrip("/")

    def configured(self):
        return bool(self._base())

    async def search(self, query, node, now, *, fetch=None):
        import xml.etree.ElementTree as ET
        fetch = fetch or _get_text
        xml = await fetch(f"{self._base()}/api/query",
                          {"search_query": f"all:{query}", "sortBy": "submittedDate",
                           "sortOrder": "descending", "max_results": PAPERS_MAX})
        root = ET.fromstring(xml)
        out = []
        for e in root.findall("a:entry", self._NS)[:PAPERS_MAX]:
            title = (e.findtext("a:title", default="", namespaces=self._NS) or "").strip()
            url = (e.findtext("a:id", default="", namespaces=self._NS) or "").strip()
            summary = (e.findtext("a:summary", default="", namespaces=self._NS) or "").strip()
            pub = (e.findtext("a:published", default="", namespaces=self._NS) or "")[:10] or None
            out.append(_candidate(summary or title, title, url, pub, self.name, node["id"]))
        return out


class _WeatherAdapter:
    name = "weather"

    def configured(self):
        from . import weather
        return weather.LAT is not None and weather.LON is not None

    async def search(self, query, node, now, *, fetch=None):
        from . import weather
        fetch = fetch or weather.current_label
        label = await fetch(weather.LAT, weather.LON)
        if not label:
            return []
        srcs = weather._forecast_sources(weather.LAT, weather.LON)
        url = srcs[0]["url"] if srcs else ""
        return [_candidate(label, f"Weather: {node.get('label')}", url, _now_date(now),
                           self.name, node["id"])]


adapters = {a.name: a for a in (
    _NewsAdapter(), _RedditAdapter(), _GithubAdapter(),
    _PapersAdapter(), _WeatherAdapter(), _LocalAdapter())}


def configured_sources():
    """The set of source names whose adapter is configured this run."""
    return {name for name, a in adapters.items() if a.configured()}


# --------------------------------------------------------------------------- orchestration


async def scout(nodes=None, now=None, llm=None, adapters=None, configured=None):
    """Fan out cheap multi-source searches over the graph's live nodes and return a flat
    candidate pool for the Analyst to rank. Selects nodes by graph math, phrases one query per
    node (LLM), runs each requested+configured source, collapses exact-URL duplicates (first
    writer wins). Returns `{candidates, scouted_nodes, skipped_sources}`, where skipped_sources
    lists the source names whose adapter is unconfigured this run."""
    import time
    now = int(time.time()) if now is None else now
    if llm is None:
        from .pulse import _vera as llm
    src = globals()["adapters"] if adapters is None else adapters
    available = configured_sources() if configured is None else set(configured)

    selected = select_live_nodes(nodes=nodes, now=now)
    candidates, seen = [], set()
    for node in selected:
        plan = await phrase_query(node, llm=llm, configured=available)
        for name in plan["sources"]:
            adapter = src.get(name)
            if adapter is None:
                continue
            for c in await adapter.search(plan["query"], node, now):
                url = c.get("url")
                if url and url in seen:
                    continue
                if url:
                    seen.add(url)
                candidates.append(c)
    return {"candidates": candidates,
            "scouted_nodes": [n["id"] for n in selected],
            "skipped_sources": sorted(set(ALLOWED_SOURCES) - available)}
