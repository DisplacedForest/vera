"""Heartbeat CURATE discipline — per-tick cap, rolling daily ceiling, duplicate skip,
a System status card per success, and outcome logging.
Run: python3 -m pytest tests/test_heartbeat_curate.py"""
import asyncio

import pytest

from routers import action_store as astore
from routers import actions
from routers import heartbeat as hb_router
from routers import heartbeat_store as hbs


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def item(url, note="worth keeping"):
    return {"verb": "kitchen.mealie_import", "args": {"url": url}, "note": note}


@pytest.fixture(autouse=True)
def harness(tmp_path, monkeypatch):
    monkeypatch.setattr(astore, "DB_PATH", str(tmp_path / "actions.db"))
    monkeypatch.setattr(hbs, "DB_PATH", str(tmp_path / "heartbeat.db"))
    cards, imports = [], []

    async def fake_status(card):
        cards.append(card)
        return {"ok": True}

    async def fake_import(args):
        imports.append(args)
        slug = args["url"].rstrip("/").rsplit("/", 1)[-1]
        return {"ok": True, "slug": slug, "name": slug.replace("-", " ").title(),
                "url": f"http://mealie.test/g/home/r/{slug}"}

    monkeypatch.setattr(hb_router, "status_card", fake_status)
    monkeypatch.setitem(actions.EXECUTORS, "kitchen.mealie_import", fake_import)
    yield {"cards": cards, "imports": imports}


def test_success_imports_logs_and_posts_card(harness):
    errors = []
    done = run(hb_router._curate([item("https://x.test/r/miso-pasta", "umami fits his tastes")], errors))
    assert done == ["Miso Pasta"]
    assert errors == []
    assert harness["imports"] == [{"url": "https://x.test/r/miso-pasta"}]
    card = harness["cards"][0]
    assert card.kind == "status" and card.category == "vera"
    assert card.title == "Imported · Miso Pasta"
    assert "umami fits his tastes" in card.body and "http://mealie.test/g/home/r/miso-pasta" in card.body
    assert any(o["kind"] == "curate" and "miso-pasta" in o["detail"] for o in hbs.recent(1))


def test_per_tick_cap(harness):
    done = run(hb_router._curate(
        [item(f"https://x.test/r/{n}") for n in ("a", "b", "c")], []))
    assert len(done) == 2                      # third item never considered
    assert len(harness["imports"]) == 2


def test_daily_ceiling(harness):
    for n in range(3):
        astore.log_auto("kitchen.mealie_import", {"url": f"https://x.test/r/{n}"}, {"ok": True})
    done = run(hb_router._curate([item("https://x.test/r/fresh")], []))
    assert done == []
    assert harness["imports"] == []            # ceiling blocked it before execution
    assert any(o["kind"] == "curate_skip" for o in hbs.recent(1))


def test_duplicate_skipped_quietly(harness):
    run(hb_router._curate([item("https://x.test/r/again")], []))
    errors = []
    done = run(hb_router._curate([item("https://x.test/r/again")], errors))
    assert done == [] and errors == []
    assert len(harness["imports"]) == 1
    assert len(harness["cards"]) == 1          # no second card for a skipped duplicate
    assert any(o["kind"] == "curate_skip" for o in hbs.recent(1))


def test_failure_recorded_no_card(monkeypatch, harness):
    async def failing(args):
        return {"ok": False, "error": "scrape failed"}

    monkeypatch.setitem(actions.EXECUTORS, "kitchen.mealie_import", failing)
    errors = []
    done = run(hb_router._curate([item("https://x.test/r/broken")], errors))
    assert done == [] and harness["cards"] == []
    assert errors and "scrape failed" in errors[0]
