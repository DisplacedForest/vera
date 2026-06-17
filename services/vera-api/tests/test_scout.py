"""Scout Agent — live-node selection, query phrasing, source adapters, orchestration.
All I/O (LLM, HTTP fetch, integrations) is injected/mocked, so the suite is offline and
deterministic. Run under pytest."""
import asyncio
import json
import os

import pytest

from routers import scout
from routers import profile_graph_store as pg


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    pg.DB_PATH = os.path.join(str(tmp_path), "graph.db")
    pg.init()
    yield


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------- live-node selection


NOW = 1_750_000_000


def _node(**kw):
    base = {"id": kw["id"], "type": kw.get("type", "interest"), "label": kw.get("label", kw["id"]),
            "facts": [], "aliases": [], "engagement": kw.get("engagement", 0.0),
            "last_engaged": kw.get("last_engaged", NOW), "state": kw.get("state"),
            "resolve_condition": None, "next_check": kw.get("next_check"),
            "confidence": None, "embedding": None, "created_at": NOW, "updated_at": NOW}
    return base


def test_selects_engaged_interest_skips_dormant():
    nodes = [
        _node(id="hot", engagement=2.0, last_engaged=NOW),
        _node(id="cold", engagement=0.1, last_engaged=NOW),
        _node(id="buried", engagement=5.0, last_engaged=NOW, state="dormant"),
    ]
    picked = [n["id"] for n in scout.select_live_nodes(nodes=nodes, now=NOW)]
    assert "hot" in picked
    assert "cold" not in picked
    assert "buried" not in picked


def test_watch_cooldown_via_next_check():
    nodes = [
        _node(id="due", type="watch", state="active", next_check=NOW - 10, engagement=0.0),
        _node(id="cooling", type="watch", state="active", next_check=NOW + 10_000, engagement=0.0),
        _node(id="open_watch", type="watch", state="active", next_check=None, engagement=0.0),
    ]
    picked = [n["id"] for n in scout.select_live_nodes(nodes=nodes, now=NOW)]
    assert "due" in picked
    assert "open_watch" in picked
    assert "cooling" not in picked


def test_open_project_and_thread_selected_resolved_skipped():
    nodes = [
        _node(id="proj", type="project", state="active", engagement=0.0),
        _node(id="done_proj", type="project", state="resolved", engagement=9.0),
        _node(id="thread", type="thread", state="open", engagement=0.0),
        _node(id="closed", type="thread", state="resolved", engagement=9.0),
    ]
    picked = [n["id"] for n in scout.select_live_nodes(nodes=nodes, now=NOW)]
    assert set(picked) == {"proj", "thread"}


def test_ranked_by_decayed_engagement_and_capped(monkeypatch):
    monkeypatch.setattr(scout, "MAX_NODES", 2)
    nodes = [
        _node(id="a", engagement=1.0, last_engaged=NOW),
        _node(id="b", engagement=3.0, last_engaged=NOW),
        _node(id="c", engagement=2.0, last_engaged=NOW),
    ]
    picked = [n["id"] for n in scout.select_live_nodes(nodes=nodes, now=NOW)]
    assert picked == ["b", "c"]


# --------------------------------------------------------------- query phrasing


def test_phrase_query_uses_llm_and_clamps_sources():
    node = _node(id="n1", label="hazelnut orchard", type="project")
    node["facts"] = [{"text": "planting 40 trees", "source": "x", "observed_at": NOW}]
    seen = {}

    async def llm(messages, temperature=0.2):
        seen["prompt"] = messages[-1]["content"]
        return '{"query": "hazelnut orchard establishment 2026", ' \
               '"sources": ["news", "papers", "bogus"]}'

    out = _run(scout.phrase_query(node, llm=llm, configured={"news", "papers", "reddit"}))
    assert out["query"] == "hazelnut orchard establishment 2026"
    assert out["sources"] == ["news", "papers"]            # bogus dropped, all clamped to configured
    assert "hazelnut orchard" in seen["prompt"]
    assert "planting 40 trees" in seen["prompt"]


def test_phrase_query_degrades_on_llm_failure():
    node = _node(id="n2", label="forest football")

    async def llm(messages, temperature=0.2):
        raise RuntimeError("model down")

    out = _run(scout.phrase_query(node, llm=llm, configured={"news", "reddit", "github"}))
    assert out["query"] == "forest football"
    assert out["sources"] == ["news", "reddit"]            # default sources, clamped to configured


def test_phrase_query_default_sources_clamped_to_configured():
    node = _node(id="n3", label="thing")

    async def llm(messages, temperature=0.2):
        return "not json at all"

    out = _run(scout.phrase_query(node, llm=llm, configured={"github"}))
    assert out["query"] == "thing"
    assert out["sources"] == []                            # no default source is configured


# --------------------------------------------------------------- source adapters

SEED = _node(id="seed1", label="hazelnuts")


def _fetch(canned):
    async def f(url, params=None):
        _fetch.last = {"url": url, "params": params}
        return canned
    return f


def test_reddit_adapter_parses_fixture():
    canned = {"data": {"children": [
        {"data": {"title": "Growing hazelnuts in zone 6", "permalink": "/r/permaculture/comments/x1/",
                  "created_utc": 1_717_000_000.0, "selftext": "any tips?"}},
        {"data": {"title": "Hazelnut harvest", "permalink": "/r/homestead/comments/x2/",
                  "created_utc": 1_717_100_000.0, "selftext": ""}},
    ]}}
    out = _run(scout.adapters["reddit"].search("hazelnuts", SEED, NOW, fetch=_fetch(canned)))
    assert len(out) == 2
    c = out[0]
    assert c["source"] == "reddit" and c["seed_node_id"] == "seed1"
    assert c["title"] == "Growing hazelnuts in zone 6"
    assert c["url"].endswith("/r/permaculture/comments/x1/")
    assert c["published_date"] == "2024-05-29"
    assert "any tips?" in c["finding_text"]


def test_github_adapter_parses_fixture():
    canned = {"items": [
        {"full_name": "nut/orchard", "html_url": "https://github.com/nut/orchard",
         "description": "orchard planner", "pushed_at": "2026-05-01T10:00:00Z"},
    ]}
    out = _run(scout.adapters["github"].search("orchard planner", SEED, NOW, fetch=_fetch(canned)))
    assert len(out) == 1
    c = out[0]
    assert c["source"] == "github" and c["seed_node_id"] == "seed1"
    assert c["url"] == "https://github.com/nut/orchard"
    assert c["published_date"] == "2026-05-01"
    assert "orchard planner" in c["finding_text"]


def test_papers_adapter_parses_atom_fixture():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Hazelnut Genome Assembly</title>
        <id>http://arxiv.org/abs/2406.00001v1</id>
        <summary>We assemble the genome.</summary>
        <published>2026-04-15T00:00:00Z</published>
      </entry>
    </feed>"""

    async def fetch_text(url, params=None):
        return xml

    out = _run(scout.adapters["papers"].search("hazelnut genome", SEED, NOW, fetch=fetch_text))
    assert len(out) == 1
    c = out[0]
    assert c["source"] == "papers" and c["seed_node_id"] == "seed1"
    assert c["title"] == "Hazelnut Genome Assembly"
    assert c["url"] == "http://arxiv.org/abs/2406.00001v1"
    assert c["published_date"] == "2026-04-15"
    assert "assemble the genome" in c["finding_text"]


def test_news_adapter_reuses_websearch():
    from routers import websearch as ws

    async def fake_search(req):
        return ws.SearchResponse(query=req.query, results=[
            ws.SearchResult(title="Hazelnut prices climb", url="https://news.example/h1",
                            content="Prices up 12%.", rendered=False, published="2026-06-10"),
        ])

    out = _run(scout.adapters["news"].search("hazelnut prices", SEED, NOW, fetch=fake_search))
    assert len(out) == 1
    c = out[0]
    assert c["source"] == "news" and c["seed_node_id"] == "seed1"
    assert c["url"] == "https://news.example/h1"
    assert c["published_date"] == "2026-06-10"
    assert "Prices up 12%" in c["finding_text"]


def test_weather_adapter_emits_one_candidate_when_coords_set(monkeypatch):
    from routers import weather
    monkeypatch.setattr(weather, "LAT", 39.5)
    monkeypatch.setattr(weather, "LON", -86.0)

    async def label_fn(lat, lon):
        return "Clear, 72F"

    loc = _node(id="locN", label="Indianapolis", type="location")
    out = _run(scout.adapters["weather"].search("Indianapolis weather", loc, NOW, fetch=label_fn))
    assert len(out) == 1
    c = out[0]
    assert c["source"] == "weather" and c["seed_node_id"] == "locN"
    assert c["published_date"] == "2025-06-15"
    assert "Clear, 72F" in c["finding_text"]


def test_configured_gating(monkeypatch):
    from routers import weather
    monkeypatch.setattr(weather, "LAT", None)
    monkeypatch.setattr(weather, "LON", None)
    monkeypatch.setattr(scout.adapters["news"], "_searxng", lambda: None)
    monkeypatch.setenv("REDDIT_BASE", "https://www.reddit.com")
    assert scout.adapters["reddit"].configured() is True
    assert scout.adapters["weather"].configured() is False
    assert scout.adapters["news"].configured() is False


# --------------------------------------------------------------- orchestration


class _FakeAdapter:
    def __init__(self, name, by_node):
        self.name = name
        self._by_node = by_node

    def configured(self):
        return True

    async def search(self, query, node, now, **kw):
        return list(self._by_node.get(node["id"], []))


def test_scout_fans_out_collapses_urls_and_surfaces_skipped():
    nodes = [
        _node(id="A", engagement=3.0, last_engaged=NOW),
        _node(id="B", engagement=2.0, last_engaged=NOW),
    ]

    async def llm(messages, temperature=0.2):
        return '{"query": "q", "sources": ["news", "reddit"]}'

    news = _FakeAdapter("news", {
        "A": [scout._candidate("a-news", "A News", "https://x/u1", "2026-06-01", "news", "A")],
        "B": [scout._candidate("b-news", "B News", "https://x/u1", "2026-06-02", "news", "B")],
    })
    reddit = _FakeAdapter("reddit", {
        "A": [scout._candidate("a-reddit", "A Reddit", "https://x/u2", "2026-06-03", "reddit", "A")],
    })

    out = _run(scout.scout(nodes=nodes, now=NOW, llm=llm,
                           adapters={"news": news, "reddit": reddit},
                           configured={"news", "reddit"}))

    urls = [c["url"] for c in out["candidates"]]
    assert urls == ["https://x/u1", "https://x/u2"]          # u1 dup across nodes collapsed
    assert out["candidates"][0]["seed_node_id"] == "A"       # first writer of u1 wins
    assert set(out["scouted_nodes"]) == {"A", "B"}
    assert set(out["skipped_sources"]) == {"github", "papers", "weather", "local"}
    for c in out["candidates"]:
        assert "published_date" in c and c["seed_node_id"]


def test_scout_isolates_a_failing_source(monkeypatch):
    nodes = [_node(id="A", engagement=3.0, last_engaged=NOW)]

    async def llm(messages, temperature=0.2):
        return '{"query": "q", "sources": ["news", "reddit"]}'

    news = _FakeAdapter("news", {
        "A": [scout._candidate("a-news", "A News", "https://x/u1", "2026-06-01", "news", "A")]})

    class _Boom:
        name = "reddit"

        def configured(self):
            return True

        async def search(self, *a, **k):
            raise RuntimeError("403 Blocked")

    out = _run(scout.scout(nodes=nodes, now=NOW, llm=llm,
                           adapters={"news": news, "reddit": _Boom()},
                           configured={"news", "reddit"}))
    assert [c["url"] for c in out["candidates"]] == ["https://x/u1"]   # news survived reddit's failure
    assert "reddit" in out["failed_sources"]                          # the failure is recorded, not fatal


def test_scout_uses_live_config_when_not_injected(monkeypatch):
    monkeypatch.setattr(scout, "configured_sources", lambda: set())

    async def llm(messages, temperature=0.2):
        return '{"query": "q", "sources": ["news"]}'

    out = _run(scout.scout(nodes=[_node(id="A", engagement=3.0, last_engaged=NOW)],
                           now=NOW, llm=llm))
    assert out["candidates"] == []
    assert set(out["skipped_sources"]) == set(scout.ALLOWED_SOURCES)
