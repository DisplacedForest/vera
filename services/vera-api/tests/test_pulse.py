import asyncio

import pytest

from routers import pulse, pulse_store


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(pulse_store, "DB_PATH", str(tmp_path / "pulse.db"))
    yield


def _inject(**kw):
    return asyncio.run(pulse._inject(**kw))


def test_inject_persists_card_fields():
    out = _inject(title="Gust warning", body="Winds at 50 mph tonight.",
                  summary="High winds tonight.", kind="weather", severity="alert",
                  sources=[{"n": 1, "title": "Forecast", "url": "https://example.org/f"}])
    assert out["ok"] and out["id"]
    card = pulse_store.get_card(out["id"])
    assert card["title"] == "Gust warning"
    assert card["body"] == "Winds at 50 mph tonight."
    assert card["summary"] == "High winds tonight."
    assert card["kind"] == "weather"
    assert card["severity"] == "alert"
    assert card["status"] == "new"
    assert card["sources"] == [{"n": 1, "title": "Forecast", "url": "https://example.org/f"}]
    assert card["situation_key"] is None


def test_inject_defaults_to_research_feed():
    out = _inject(title="t", body="b")
    card = pulse_store.get_card(out["id"])
    assert card["kind"] == "research"
    assert card["severity"] is None
    assert card["provenance"] == "scheduled"


def test_inject_persists_situation_key():
    out = _inject(title="t", body="b", kind="custom", situation_key="sensor.fridge:hi")
    card = pulse_store.get_card(out["id"])
    assert card["situation_key"] == "sensor.fridge:hi"


def test_store_does_not_dedup_by_situation_key():
    a = _inject(title="t1", body="b", kind="custom", situation_key="k")
    b = _inject(title="t2", body="b", kind="custom", situation_key="k")
    assert a["id"] != b["id"]
    keyed = [c for c in pulse_store.list_cards() if c.get("situation_key") == "k"]
    assert len(keyed) == 2


def test_situation_key_migrates_existing_db():
    pulse_store.init()
    with pulse_store._conn() as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(cards)").fetchall()]
    assert "situation_key" in cols
