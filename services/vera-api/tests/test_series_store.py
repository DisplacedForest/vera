import asyncio
import time

import pytest

from routers import home_events, home_events_store, series_store


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(series_store, "DB_PATH", str(tmp_path / "series.db"))
    monkeypatch.setattr(home_events_store, "DB_PATH", str(tmp_path / "home_events.db"))
    yield


NOW = int(time.time())


def _fill(entity="sensor.kitchen_temp", n=5, step=60, start=None):
    start = NOW - n * step if start is None else start
    for i in range(n):
        series_store.insert(entity, start + i * step, 20.0 + i)
    return start


def test_insert_idempotent():
    series_store.insert("sensor.t", NOW, 21.5)
    series_store.insert("sensor.t", NOW, 99.9)
    pts = series_store.series("sensor.t")
    assert pts == [(NOW, 21.5)]


def test_series_range_sort_limit():
    start = _fill(n=5)
    pts = series_store.series("sensor.kitchen_temp")
    assert [p[1] for p in pts] == [20.0, 21.0, 22.0, 23.0, 24.0]
    assert pts == sorted(pts)
    mid = series_store.series("sensor.kitchen_temp", since=start + 60, until=start + 180)
    assert [p[1] for p in mid] == [21.0, 22.0, 23.0]
    assert len(series_store.series("sensor.kitchen_temp", limit=2)) == 2


def test_insert_many_counts_new_rows_only():
    rows = [("sensor.a", NOW, 1.0), ("sensor.a", NOW + 1, 2.0)]
    assert series_store.insert_many(rows) == 2
    assert series_store.insert_many(rows) == 0


def test_entities_counts_and_min_points():
    _fill("sensor.a", n=3)
    _fill("sensor.b", n=1)
    ents = {e["entity_id"]: e for e in series_store.entities()}
    assert ents["sensor.a"]["count"] == 3
    assert ents["sensor.b"]["count"] == 1
    assert ents["sensor.a"]["first_ts"] < ents["sensor.a"]["last_ts"]
    assert [e["entity_id"] for e in series_store.entities(min_points=2)] == ["sensor.a"]


def test_latest():
    assert series_store.latest("sensor.none") is None
    start = _fill(n=3)
    assert series_store.latest("sensor.kitchen_temp") == (start + 120, 22.0)


def test_purge_cutoff():
    old = NOW - 400 * 86400
    series_store.insert("sensor.a", old, 1.0)
    series_store.insert("sensor.a", NOW, 2.0)
    assert series_store.purge() == 1
    assert series_store.series("sensor.a") == [(NOW, 2.0)]
    series_store.insert("sensor.a", NOW - 2 * 86400, 1.5)
    assert series_store.purge(retain_days=1) == 1


def test_backfill_from_events_once():
    for i, (state, expect) in enumerate((("21.5", True), ("on", False),
                                         ("unavailable", False), ("22.5", True))):
        home_events_store.insert({"ts": NOW - 100 + i, "event_type": "state_changed",
                                  "entity_id": "sensor.porch", "domain": "sensor",
                                  "old_state": None, "new_state": state,
                                  "attrs": None, "context": None})
    home_events_store.insert({"ts": NOW, "event_type": "state_changed",
                              "entity_id": "light.porch", "domain": "light",
                              "old_state": "off", "new_state": "on",
                              "attrs": None, "context": None})
    series_store.init()
    pts = series_store.series("sensor.porch")
    assert [p[1] for p in pts] == [21.5, 22.5]
    assert series_store.entities(min_points=1) and len(series_store.entities()) == 1
    series_store.insert("sensor.porch", NOW + 5, 23.0)
    series_store.init()
    assert len(series_store.series("sensor.porch")) == 3


def _msg(entity, new_state, ts=None):
    return {"event": {"event_type": "state_changed",
                      "time_fired": "2026-07-08T12:00:00+00:00" if ts is None else ts,
                      "data": {"entity_id": entity,
                               "old_state": {"state": "0"},
                               "new_state": {"state": new_state, "attributes": {}}},
                      "context": None}}


def test_record_tees_numeric_sensor():
    home_events._record(_msg("sensor.kitchen_temp", "21.7"))
    pts = series_store.series("sensor.kitchen_temp")
    assert len(pts) == 1 and pts[0][1] == 21.7


def test_record_skips_non_numeric_and_non_sensor():
    home_events._record(_msg("sensor.door", "unavailable"))
    home_events._record(_msg("sensor.mode", "eco"))
    home_events._record(_msg("light.kitchen", "42"))
    assert series_store.entities() == []


def test_record_honors_ignore(monkeypatch):
    monkeypatch.setattr(home_events, "_IGNORE", ["sensor.noisy"])
    home_events._record(_msg("sensor.noisy_temp", "21.0"))
    assert series_store.entities() == []


def test_series_endpoints():
    assert asyncio.run(home_events.series_index())["entities"] == []
    assert asyncio.run(home_events.series_points("sensor.a"))["points"] == []
    start = _fill("sensor.a", n=3)
    idx = asyncio.run(home_events.series_index())
    assert idx["entities"][0]["entity_id"] == "sensor.a"
    out = asyncio.run(home_events.series_points("sensor.a", since=start + 60))
    assert [p[1] for p in out["points"]] == [21.0, 22.0]
