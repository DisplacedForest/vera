"""Heartbeat for-you discipline — skipped candidates enter the don't-repeat list, cool
their topic and proposing interest, and interests on cooldown are withheld from the
candidate prompt; a shipped card stamps its interest. Run: python3 -m pytest tests/test_heartbeat_foryou.py
"""
import asyncio
import json
import time

import pytest

from routers import heartbeat as hb_router
from routers import heartbeat_store as hbs
from routers import vera_interests_store as vi


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


INTERESTS = [{"topic": "Ashvale Rovers", "gloss": "the football club"}]


@pytest.fixture(autouse=True)
def _harness(tmp_path, monkeypatch):
    monkeypatch.setattr(vi, "DB_PATH", str(tmp_path / "interests.db"))
    monkeypatch.setattr(hbs, "DB_PATH", str(tmp_path / "heartbeat.db"))

    async def users():
        return [{"id": "u1", "name": "Z"}]

    async def vision(pause):
        return None

    monkeypatch.setattr(hb_router, "_active_users", users)
    monkeypatch.setattr(hb_router, "_vision", vision)
    monkeypatch.setattr(hb_router, "_recent_for_user", lambda uid: [])
    monkeypatch.setattr(hb_router.up, "get",
                        lambda uid: {"persona": None, "interests": INTERESTS})
    monkeypatch.setattr(hb_router.up, "interests", lambda uid: list(INTERESTS))
    yield


def _vera_for(decide):
    """Dispatch on the system prompt: candidate -> `decide`, the two gates -> pass."""
    async def fake(messages, temperature=0.4):
        sys_p = messages[0]["content"]
        if "between briefings" in sys_p:
            return json.dumps(decide)
        if "ACTUALLY serves" in sys_p:
            return '{"related": true, "link": "club news"}'
        if "briefing_worthy" in sys_p:
            return '{"briefing_worthy": true}'
        return "{}"
    return fake


def test_skipped_topics_enter_dont_repeat_list(monkeypatch):
    seen = {}

    async def fake(messages, temperature=0.4):
        if "between briefings" in messages[0]["content"]:
            seen["usr"] = messages[1]["content"]
            return '{"surface": false}'
        return "{}"

    monkeypatch.setattr(hb_router, "_vera", fake)
    recent = [{"ts": 0, "kind": "foryou_skip", "detail": "u1:UniFi OS 4.1 release", "extra": None}]
    assert run(hb_router._for_you("now", recent)) is None
    assert "UniFi OS 4.1 release" in seen["usr"]


def test_recently_skipped_topic_is_not_re_researched(monkeypatch):
    monkeypatch.setattr(hb_router, "_vera", _vera_for(
        {"surface": True, "interest": "Ashvale Rovers",
         "topic": "Ashvale's cup run", "query": "ashvale cup"}))
    researched = []

    async def fake_research(topic, **kw):
        researched.append(topic)
        return None

    monkeypatch.setattr(hb_router, "research_topic", fake_research)
    recent = [{"ts": 0, "kind": "foryou_skip", "detail": "u1:Ashvale's cup run", "extra": None}]
    assert run(hb_router._for_you("now", recent)) is None
    assert researched == []


def test_dedup_skip_cools_topic_and_interest(monkeypatch):
    monkeypatch.setattr(hb_router, "_vera", _vera_for(
        {"surface": True, "interest": "Ashvale Rovers",
         "topic": "Ashvale's cup run", "query": "ashvale cup"}))

    async def fake_research(topic, *, who, user_id, idx, provenance, errors):
        errors.append("skipped (already covered): Ashvale's cup run ≈ Old card")
        return None

    monkeypatch.setattr(hb_router, "research_topic", fake_research)
    assert run(hb_router._for_you("now", [])) is None
    rows = {r["topic"]: r for r in vi.all_interests()}
    now = int(time.time())
    assert rows["Ashvale's cup run"]["cooldown_until"] > now
    assert rows["Ashvale Rovers"]["cooldown_until"] > now
    skips = [o for o in hbs.recent(1) if o["kind"] == "foryou_skip"]
    assert skips and skips[0]["detail"] == "u1:Ashvale's cup run"


def test_cooled_interest_is_withheld(monkeypatch):
    vi.observe("Ashvale Rovers")
    vi.touch("Ashvale Rovers")
    called = {}

    async def fake(messages, temperature=0.4):
        called["model"] = True
        return '{"surface": false}'

    monkeypatch.setattr(hb_router, "_vera", fake)
    # the only interest is cooling off -> quiet tick, no candidate prompt at all
    assert run(hb_router._for_you("now", [])) is None
    assert "model" not in called


def test_shipped_card_stamps_its_interest(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(hb_router, "_vera", _vera_for(
        {"surface": True, "interest": "Ashvale Rovers",
         "topic": "Ashvale's cup run", "query": "ashvale cup"}))

    async def fake_research(topic, **kw):
        return {"title": "Ashvale's cup run"}

    async def fake_search(req):
        return SimpleNamespace(results=[])

    monkeypatch.setattr(hb_router, "research_topic", fake_research)
    monkeypatch.setattr(hb_router, "web_search", fake_search)
    out = run(hb_router._for_you("now", []))
    assert out == {"user": "Z", "topic": "Ashvale's cup run"}
    rows = {r["topic"]: r for r in vi.all_interests()}
    assert rows["Ashvale Rovers"]["cooldown_until"] > int(time.time())
