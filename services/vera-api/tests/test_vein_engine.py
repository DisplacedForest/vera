import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from routers import pulse_store, vein_defs, vein_engine, vein_engine_store, websearch


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(pulse_store, "DB_PATH", str(tmp_path / "pulse.db"))
    monkeypatch.setattr(vein_engine_store, "DB_PATH", str(tmp_path / "engine.db"))
    monkeypatch.setattr(vein_defs, "CUSTOM_DIR", str(tmp_path / "veins.d"))
    yield


CTX = {"kind": "t", "options": {"beat": "earthquakes"}, "providers": {"gauge": "https://g.example/x.json"}}


def _run(coro):
    return asyncio.run(coro)


def test_template_substitutes_options_and_providers():
    assert vein_engine.template("news about {options.beat}", CTX) == "news about earthquakes"
    assert vein_engine.template("{providers.gauge}", CTX) == "https://g.example/x.json"
    assert vein_engine.template(42, CTX) == 42


def test_template_missing_reference_fails():
    with pytest.raises(ValueError, match="options.nope"):
        vein_engine.template("{options.nope}", CTX)


def _stub_search(monkeypatch, results):
    async def fake(req):
        fake.query = req.query
        return SimpleNamespace(results=[SimpleNamespace(**r) for r in results])
    monkeypatch.setattr(websearch, "search", fake)
    return fake


def test_web_search_block_shapes_items(monkeypatch):
    fake = _stub_search(monkeypatch, [
        {"title": "A", "url": "https://a", "content": "alpha", "published": "2026-07-01"}])
    items = _run(vein_engine._run_web_search([], {"query": "{options.beat} today"}, CTX))
    assert fake.query == "earthquakes today"
    assert items == [{"key": "https://a", "title": "A", "url": "https://a",
                      "content": "alpha", "published": "2026-07-01"}]


def test_web_search_requires_query():
    with pytest.raises(vein_engine.BlockError):
        _run(vein_engine._run_web_search([], {}, CTX))


def _stub_get(monkeypatch, status, body):
    async def fake(url):
        fake.url = url
        return status, body
    monkeypatch.setattr(vein_engine, "_get", fake)
    return fake


def test_http_fetch_extracts_numeric_leaf(monkeypatch):
    _stub_get(monkeypatch, 200, json.dumps({"data": {"level": "21.7"}}))
    items = _run(vein_engine._run_http_fetch([], {"url": "{providers.gauge}",
                                                  "extract": "$.data.level"}, CTX))
    assert items[0]["value"] == 21.7
    assert items[0]["key"] == "https://g.example/x.json#$.data.level"


def test_http_fetch_without_extract_keeps_text(monkeypatch):
    _stub_get(monkeypatch, 200, "plain   body " + "x" * 5000)
    items = _run(vein_engine._run_http_fetch([], {"url": "https://t.example"}, CTX))
    assert items[0]["content"].startswith("plain body")
    assert len(items[0]["content"]) <= vein_engine.FETCH_CHARS


def test_http_fetch_errors(monkeypatch):
    _stub_get(monkeypatch, 500, "boom")
    with pytest.raises(vein_engine.BlockError, match="HTTP 500"):
        _run(vein_engine._run_http_fetch([], {"url": "https://t.example"}, CTX))
    _stub_get(monkeypatch, 200, json.dumps({"a": 1}))
    with pytest.raises(vein_engine.BlockError, match="not found"):
        _run(vein_engine._run_http_fetch([], {"url": "https://t.example", "extract": "b.c"}, CTX))


def test_ha_state_requires_integration(monkeypatch):
    from routers import integrations
    monkeypatch.setattr(integrations, "integration", lambda _id: None)
    with pytest.raises(vein_engine.BlockError, match="not connected"):
        _run(vein_engine._run_ha_state([], {"entity_id": "sensor.x"}, CTX))


def test_ha_state_parses_float(monkeypatch):
    from routers import integrations
    monkeypatch.setattr(integrations, "integration",
                        lambda _id: {"url": "http://ha.example", "token": "tok"})
    async def fake(url, token, entity_id):
        return "37.5"
    monkeypatch.setattr(vein_engine, "_ha_get", fake)
    items = _run(vein_engine._run_ha_state([], {"entity_id": "sensor.x"}, CTX))
    assert items[0] == {"key": "sensor.x", "title": "sensor.x", "content": "37.5", "value": 37.5}


def test_trip_band_crossings():
    items = [{"key": "a", "value": 25.0}, {"key": "b", "value": 10.0},
             {"key": "c", "value": 17.0}, {"key": "d", "content": "n/a"}]
    out = _run(vein_engine._run_trip_band(items, {"hi": 21.5, "lo": 12, "severity": "critical"}, CTX))
    assert [(i["key"], i["side"]) for i in out] == [("a:hi", "hi"), ("b:lo", "lo")]
    assert all(i["severity"] == "critical" for i in out)


def test_trip_band_requires_a_bound():
    with pytest.raises(vein_engine.BlockError):
        _run(vein_engine._run_trip_band([], {}, CTX))


def _fake_vera(keep=(0,)):
    async def f(messages, **kw):
        if "verdicts" in messages[0]["content"]:
            return json.dumps({"verdicts": [
                {"index": i, "keep": i in keep, "reason": "r"} for i in range(10)]})
        return "HEADLINE: Gauge rising\nSUMMARY: The river gauge crossed its band.\n===\nBody text here."
    return f


def test_llm_judge_drops_non_keepers(monkeypatch):
    monkeypatch.setattr(vein_engine, "_vera", _fake_vera(keep=(1,)))
    items = [{"key": "a", "title": "A"}, {"key": "b", "title": "B"}]
    out = _run(vein_engine._run_llm_judge(items, {"bar": "matters to {options.beat}"}, CTX))
    assert [i["key"] for i in out] == ["b"]


def test_llm_compose_parses_headline_summary_body(monkeypatch):
    monkeypatch.setattr(vein_engine, "_vera", _fake_vera())
    out = _run(vein_engine._run_llm_compose([{"key": "a", "title": "A", "value": 22.0}], {}, CTX))
    assert out[0]["headline"] == "Gauge rising"
    assert out[0]["summary"] == "The river gauge crossed its band."
    assert out[0]["body"] == "Body text here."


def test_validate_pipeline_names_the_step():
    errors = vein_engine.validate_pipeline({"pipeline": [
        {"block": "web_search", "params": {"query": "x"}},
        {"block": "teleport"},
        {"block": "trip_band", "params": {}},
    ]})
    assert errors == ["step 1: unknown block 'teleport'",
                      "step 2: trip_band needs params.hi or params.lo"]


def test_register_extends_blocks():
    async def custom(items, params, ctx):
        return items
    vein_engine.register("custom_source", custom)
    try:
        assert vein_engine.validate_pipeline({"pipeline": [{"block": "custom_source"}]}) == []
    finally:
        vein_engine.BLOCKS.pop("custom_source", None)


def _save_watcher(kind="newswatch"):
    return vein_defs.save_custom({
        "kind": kind, "label": "News watch", "icon": "eye",
        "options": [{"group": "Beat", "fields": [
            {"id": "beat", "label": "Beat", "type": "text", "default": "earthquakes"}]}],
        "pipeline": [
            {"block": "web_search", "params": {"query": "{options.beat}"}},
            {"block": "llm_judge", "params": {"bar": "matters"}},
            {"block": "llm_compose"},
        ],
        "schedule": "0 */6 * * *",
    })


def _save_monitor(kind="gauge"):
    return vein_defs.save_custom({
        "kind": kind, "label": "Gauge", "icon": "water.waves",
        "pipeline": [
            {"block": "http_fetch", "params": {"url": "https://g.example/x.json",
                                               "extract": "$.level"}},
            {"block": "trip_band", "params": {"hi": 21.5}},
        ],
        "schedule": "*/30 * * * *",
    })


def _active(kind):
    return [c for c in pulse_store.list_cards() if c["kind"] == kind]


def test_watcher_run_posts_and_seen_suppresses(monkeypatch):
    _save_watcher()
    _stub_search(monkeypatch, [{"title": "A", "url": "https://a", "content": "x", "published": None}])
    monkeypatch.setattr(vein_engine, "_vera", _fake_vera(keep=(0,)))
    out = _run(vein_engine.run_vein("newswatch", manual=True))
    assert out == {"ok": True, "situations": 1, "cards": 1}
    cards = _active("newswatch")
    assert len(cards) == 1
    assert cards[0]["situation_key"] == "https://a"
    assert cards[0]["severity"] == "notice"
    assert cards[0]["title"] == "Gauge rising"
    again = _run(vein_engine.run_vein("newswatch", manual=True))
    assert again["situations"] == 0
    assert len(_active("newswatch")) == 1


def test_watcher_realerts_after_decay(monkeypatch):
    _save_watcher()
    _stub_search(monkeypatch, [{"title": "A", "url": "https://a", "content": "x", "published": None}])
    monkeypatch.setattr(vein_engine, "_vera", _fake_vera(keep=(0,)))
    vein_engine_store.record_seen("newswatch", ["https://a"],
                                  ts=int(time.time()) - 8 * 86400)
    out = _run(vein_engine.run_vein("newswatch", manual=True))
    assert out["situations"] == 1


def test_monitor_retires_cleared_and_upserts_standing(monkeypatch):
    _save_monitor()
    _stub_get(monkeypatch, 200, json.dumps({"level": 25}))
    out = _run(vein_engine.run_vein("gauge"))
    assert out == {"ok": True, "situations": 1, "cards": 1}
    first = _active("gauge")
    assert len(first) == 1 and first[0]["severity"] == "alert"
    _run(vein_engine.run_vein("gauge"))
    second = _active("gauge")
    assert len(second) == 1 and second[0]["id"] != first[0]["id"]
    _stub_get(monkeypatch, 200, json.dumps({"level": 10}))
    cleared = _run(vein_engine.run_vein("gauge"))
    assert cleared["situations"] == 0
    assert _active("gauge") == []


def test_bookmarked_cards_survive_reconciliation(monkeypatch):
    _save_monitor()
    _stub_get(monkeypatch, 200, json.dumps({"level": 25}))
    _run(vein_engine.run_vein("gauge"))
    card = _active("gauge")[0]
    pulse_store.set_status(card["id"], "bookmarked")
    _stub_get(monkeypatch, 200, json.dumps({"level": 10}))
    _run(vein_engine.run_vein("gauge"))
    assert pulse_store.get_card(card["id"])["status"] == "bookmarked"


def test_floor_skips_scheduled_but_not_manual(monkeypatch):
    _save_watcher()
    _stub_search(monkeypatch, [{"title": "A", "url": "https://a", "content": "x", "published": None}])
    monkeypatch.setattr(vein_engine, "_vera", _fake_vera(keep=(0,)))
    vein_engine_store.mark_run("newswatch")
    out = _run(vein_engine.run_vein("newswatch"))
    assert out["skipped"] == "schedule floor"
    manual = _run(vein_engine.run_vein("newswatch", manual=True))
    assert manual["situations"] == 1


def test_dry_run_persists_nothing(monkeypatch):
    _save_watcher()
    _stub_search(monkeypatch, [{"title": "A", "url": "https://a", "content": "x", "published": None}])
    monkeypatch.setattr(vein_engine, "_vera", _fake_vera(keep=(0,)))
    out = _run(vein_engine.run_vein("newswatch", dry_run=True))
    assert out["dry_run"] and out["situations"] == 1
    assert out["cards"][0]["situation_key"] == "https://a"
    assert _active("newswatch") == []
    assert vein_engine_store.filter_unseen("newswatch", ["https://a"], 7 * 86400) == ["https://a"]
    assert vein_engine_store.last_run("newswatch") is None


def test_block_failure_aborts_with_block_named(monkeypatch):
    _save_monitor()
    _stub_get(monkeypatch, 500, "boom")
    out = _run(vein_engine.run_vein("gauge"))
    assert out["ok"] is False and out["block"] == "http_fetch"
    assert _active("gauge") == []


def test_run_vein_unknown_kind():
    out = _run(vein_engine.run_vein("nope"))
    assert out["ok"] is False


def test_dynamic_jobs_reflect_pipeline_definitions():
    _save_monitor()
    jobs = vein_engine.dynamic_jobs()
    assert "vein_gauge" in jobs
    label, cron, handler = jobs["vein_gauge"]
    assert label == "Gauge vein run"
    assert cron == "*/30 * * * *"
    vein_defs.delete_custom("gauge")
    assert "vein_gauge" not in vein_engine.dynamic_jobs()
