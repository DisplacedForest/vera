import asyncio

import pytest

from routers import media_curation, overseerr, vein_engine

CTX = {"kind": "media", "options": {"taste": "no anime, english only", "cap": 2}, "providers": {}}

POOL = [
    {"media_type": "movie", "id": 11, "title": "Heat", "year": 1995,
     "overview": "A heist crew and a detective circle each other.", "poster": "https://p/11"},
    {"media_type": "tv", "id": 22, "title": "Severance", "year": 2022,
     "overview": "Employees split their memories.", "poster": "https://p/22"},
    {"media_type": "movie", "id": 33, "title": "Ronin", "year": 1998,
     "overview": "Mercenaries chase a case.", "poster": None},
]


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _stub_media(monkeypatch):
    async def discover():
        return [dict(m) for m in POOL]
    async def zeitgeist(taste=None):
        zeitgeist.taste = taste
        return []
    async def link(mt, mid):
        return f"https://tmdb/{mt}/{mid}"
    monkeypatch.setattr(media_curation, "_discover_pool", discover)
    monkeypatch.setattr(media_curation, "_zeitgeist_pool", zeitgeist)
    monkeypatch.setattr(overseerr, "detail_link", link)
    monkeypatch.setattr(media_curation.mstore, "seen_keys", lambda: set())
    yield zeitgeist


def test_media_blocks_registered_as_watchers():
    for name in ("media_candidates", "media_digest"):
        assert name in vein_engine.BLOCKS
        assert name not in vein_engine.MONITOR_BLOCKS


def test_media_candidates_shapes_pool_and_threads_taste(_stub_media):
    items = _run(media_curation._block_media_candidates([], {}, CTX))
    assert [i["key"] for i in items] == ["movie:11", "tv:22", "movie:33"]
    assert items[0]["title"] == "Heat (1995)"
    assert items[0]["content"].startswith("[movie]")
    assert _stub_media.taste == "no anime, english only"


def test_media_candidates_neutral_taste_when_blank(_stub_media):
    ctx = {"kind": "media", "options": {}, "providers": {}}
    _run(media_curation._block_media_candidates([], {}, ctx))
    assert _stub_media.taste == media_curation._NEUTRAL_TASTE


def test_media_digest_caps_and_builds_rows(monkeypatch):
    async def fake_build(rows):
        return [{"title": r.title, "subtitle": r.subtitle, "state": "pending"} for r in rows]
    from routers import actions
    monkeypatch.setattr(actions, "_build_digest_items", fake_build)
    candidates = _run(media_curation._block_media_candidates([], {}, CTX))
    candidates[0]["judge_reason"] = "modern classic"
    out = _run(media_curation._block_media_digest(candidates, {}, CTX))
    assert len(out) == 1
    digest = out[0]
    assert digest["key"].startswith("digest:") and "-W" in digest["key"]
    assert digest["title"] == "Worth adding this week"
    assert len(digest["items"]) == 2
    assert digest["items"][0]["subtitle"] == "1995 · Movie · modern classic"
    assert digest["content"] == ("This week I'd add 2 to the library. "
                                 "Tap add to grab each, or skip to pass.")


def test_media_digest_quiet_on_empty():
    assert _run(media_curation._block_media_digest([], {}, CTX)) == []
