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


def test_llm_judge_drops_non_keepers_and_keeps_reasons(monkeypatch):
    monkeypatch.setattr(vein_engine, "_vera", _fake_vera(keep=(1,)))
    items = [{"key": "a", "title": "A"}, {"key": "b", "title": "B"}]
    out = _run(vein_engine._run_llm_judge(items, {"bar": "matters to {options.beat}"}, CTX))
    assert [i["key"] for i in out] == ["b"]
    assert out[0]["judge_reason"] == "r"


def test_llm_compose_parses_headline_summary_body(monkeypatch):
    monkeypatch.setattr(vein_engine, "_vera", _fake_vera())
    out = _run(vein_engine._run_llm_compose([{"key": "a", "title": "A", "value": 22.0}], {}, CTX))
    assert out[0]["headline"] == "Gauge rising"
    assert out[0]["summary"] == "The river gauge crossed its band."
    assert out[0]["body"] == "Body text here."


def test_llm_compose_honors_style_param(monkeypatch):
    async def spy(messages, **kw):
        spy.sys = messages[0]["content"]
        return "HEADLINE: H\nSUMMARY: S.\n===\nB."
    monkeypatch.setattr(vein_engine, "_vera", spy)
    _run(vein_engine._run_llm_compose([{"key": "a", "title": "A"}],
                                      {"style": "lead with {options.beat}"}, CTX))
    assert "lead with earthquakes" in spy.sys


def test_card_fields_pass_sources():
    fields = vein_engine._card_fields({"key": "k", "title": "T",
                                       "sources": [{"n": 1, "title": "Full forecast", "url": "https://f"}]})
    assert fields["sources"] == [{"n": 1, "title": "Full forecast", "url": "https://f"}]


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


def test_monitor_retires_cleared_and_leaves_standing_untouched(monkeypatch):
    _save_monitor()
    _stub_get(monkeypatch, 200, json.dumps({"level": 25}))
    out = _run(vein_engine.run_vein("gauge"))
    assert out == {"ok": True, "situations": 1, "cards": 1}
    first = _active("gauge")
    assert len(first) == 1 and first[0]["severity"] == "alert"
    assert first[0]["change_set"]
    again = _run(vein_engine.run_vein("gauge"))
    assert again == {"ok": True, "situations": 1, "cards": 0, "standing": 1}
    second = _active("gauge")
    assert len(second) == 1 and second[0]["id"] == first[0]["id"]
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


def test_floor_marks_only_when_llm_ran_on_items(monkeypatch):
    _save_watcher()
    _stub_search(monkeypatch, [])
    monkeypatch.setattr(vein_engine, "_vera", _fake_vera())
    out = _run(vein_engine.run_vein("newswatch"))
    assert out["situations"] == 0
    assert vein_engine_store.last_run("newswatch") is None


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


def _register_feed(feed, name="feed_src", monitor=True):
    async def src(items, params, ctx):
        return items + [dict(i) for i in feed]
    vein_engine.register(name, src, monitor=monitor)


def _unregister(name="feed_src"):
    vein_engine.BLOCKS.pop(name, None)
    vein_engine.MONITOR_BLOCKS.discard(name)


def _counting_vera():
    async def f(messages, **kw):
        f.calls += 1
        return "HEADLINE: Service down\nSUMMARY: A watched service is down.\n===\nBody."
    f.calls = 0
    return f


def _save_feed_monitor(kind="sys"):
    return vein_defs.save_custom({
        "kind": kind, "label": "Sys", "icon": "gearshape",
        "pipeline": [{"block": "feed_src"}, {"block": "llm_compose"}],
        "schedule": "*/30 * * * *",
    })


def test_registered_monitor_block_flags_pipeline():
    _register_feed([], name="mon_probe")
    try:
        assert vein_engine.is_monitor([{"block": "mon_probe"}])
        assert not vein_engine.is_monitor([{"block": "web_search"}])
    finally:
        _unregister("mon_probe")


def test_card_fields_pass_category_items_and_signature():
    fields = vein_engine._card_fields({"key": "k", "title": "T", "content": "c",
                                       "category": "health", "items": [{"row": 1}]})
    assert fields["category"] == "health"
    assert fields["items"] == [{"row": 1}]
    assert fields["change_set"]


def test_monitor_standing_unchanged_card_is_untouched(monkeypatch):
    feed = [{"key": "health:owui", "title": "owui", "content": "down",
             "severity": "alert", "category": "health"}]
    _register_feed(feed)
    try:
        _save_feed_monitor()
        fake = _counting_vera()
        monkeypatch.setattr(vein_engine, "_vera", fake)
        out = _run(vein_engine.run_vein("sys", manual=True))
        assert out == {"ok": True, "situations": 1, "cards": 1}
        first = _active("sys")[0]
        assert first["category"] == "health" and first["change_set"]
        assert fake.calls == 1
        again = _run(vein_engine.run_vein("sys", manual=True))
        assert again == {"ok": True, "situations": 1, "cards": 0, "standing": 1}
        assert fake.calls == 1
        assert _active("sys")[0]["id"] == first["id"]
        feed[0]["content"] = "down for 40 minutes"
        changed = _run(vein_engine.run_vein("sys", manual=True))
        assert changed["cards"] == 1
        assert fake.calls == 2
        latest = _active("sys")[0]
        assert latest["id"] != first["id"]
        assert latest["change_set"] != first["change_set"]
    finally:
        _unregister()


def test_monitor_standing_resurfaces_on_day_rollover(monkeypatch):
    feed = [{"key": "health:owui", "title": "owui", "content": "down", "severity": "alert"}]
    _register_feed(feed)
    try:
        _save_feed_monitor()
        fake = _counting_vera()
        monkeypatch.setattr(vein_engine, "_vera", fake)
        _run(vein_engine.run_vein("sys", manual=True))
        card = _active("sys")[0]
        pulse_store.insert_card({**card, "status": "seen", "day": "2000-01-01"})
        _run(vein_engine.run_vein("sys", manual=True))
        after = _active("sys")[0]
        assert after["id"] == card["id"]
        assert after["day"] != "2000-01-01"
        assert after["status"] == "new"
        assert fake.calls == 1
    finally:
        _unregister()


def test_monitor_retires_cleared_but_keeps_standing(monkeypatch):
    feed = [{"key": "health:owui", "title": "owui", "content": "down", "severity": "alert"},
            {"key": "health:searxng", "title": "searxng", "content": "down", "severity": "alert"}]
    _register_feed(feed)
    try:
        _save_feed_monitor()
        monkeypatch.setattr(vein_engine, "_vera", _counting_vera())
        _run(vein_engine.run_vein("sys", manual=True))
        assert len(_active("sys")) == 2
        del feed[1]
        out = _run(vein_engine.run_vein("sys", manual=True))
        assert out == {"ok": True, "situations": 1, "cards": 0, "standing": 1}
        remaining = _active("sys")
        assert [c["situation_key"] for c in remaining] == ["health:owui"]
    finally:
        _unregister()


def test_dry_run_counts_standing_without_touching(monkeypatch):
    feed = [{"key": "health:owui", "title": "owui", "content": "down", "severity": "alert"}]
    _register_feed(feed)
    try:
        _save_feed_monitor()
        fake = _counting_vera()
        monkeypatch.setattr(vein_engine, "_vera", fake)
        _run(vein_engine.run_vein("sys", manual=True))
        out = _run(vein_engine.run_vein("sys", dry_run=True))
        assert out["dry_run"] and out["standing"] == 1 and out["cards"] == []
        assert out["situations"] == 1
        assert fake.calls == 1
    finally:
        _unregister()


def test_run_definition_executes_unsaved_draft_with_steps(monkeypatch):
    _stub_get(monkeypatch, 200, json.dumps({"level": 25}))
    defn = {
        "kind": "draftgauge", "label": "Draft", "icon": "water.waves",
        "pipeline": [
            {"block": "http_fetch", "params": {"url": "https://g.example/x.json",
                                               "extract": "level"}},
            {"block": "trip_band", "params": {"hi": 21.5}},
        ],
        "schedule": "*/30 * * * *",
    }
    out = _run(vein_engine.run_definition(defn, dry_run=True))
    assert out["ok"] and out["dry_run"] and out["situations"] == 1
    assert out["steps"] == [{"block": "http_fetch", "items": 1},
                            {"block": "trip_band", "items": 1}]
    assert pulse_store.list_cards() == []
    assert vein_engine_store.last_run("draftgauge") is None


def test_run_definition_failure_carries_partial_steps(monkeypatch):
    _stub_get(monkeypatch, 200, json.dumps({"level": 25}))
    defn = {
        "kind": "draftgauge", "label": "Draft", "icon": "water.waves",
        "pipeline": [
            {"block": "http_fetch", "params": {"url": "https://g.example/x.json",
                                               "extract": "level"}},
            {"block": "trip_band", "params": {}},
        ],
        "schedule": "*/30 * * * *",
    }
    out = _run(vein_engine.run_definition(defn, dry_run=True))
    assert out["ok"] is False and out["block"] == "trip_band"
    assert out["steps"] == [{"block": "http_fetch", "items": 1}]


def test_run_definition_resolves_unsaved_provider_defaults(monkeypatch):
    _stub_get(monkeypatch, 200, json.dumps({"level": 25}))
    defn = {
        "kind": "draftgauge", "label": "Draft", "icon": "water.waves",
        "providers": [{"id": "gauge_url", "label": "Gauge",
                       "default": "https://g.example/x.json"}],
        "pipeline": [
            {"block": "http_fetch", "params": {"url": "{providers.gauge_url}",
                                               "extract": "level"}},
            {"block": "trip_band", "params": {"hi": 21.5}},
        ],
        "schedule": "*/30 * * * *",
    }
    out = _run(vein_engine.run_definition(defn, dry_run=True))
    assert out["ok"] is True and out["situations"] == 1


def test_monitor_leaves_keyless_cards_alone(monkeypatch):
    _save_monitor()
    _stub_get(monkeypatch, 200, json.dumps({"level": 25}))
    _run(vein_engine.run_vein("gauge"))
    row = dict(pulse_store.list_cards()[0])
    row["id"] = "foreign-1"
    row["situation_key"] = None
    row["title"] = "watch update"
    pulse_store.insert_card(row)
    _stub_get(monkeypatch, 200, json.dumps({"level": 10}))
    _run(vein_engine.run_vein("gauge"))
    remaining = _active("gauge")
    assert [c["id"] for c in remaining] == ["foreign-1"]


def test_watch_topics_parse():
    body = "Prose here.\n\nWatching: port backlogs, diesel prices; grain futures and nothing"
    assert vein_engine._watch_topics(body) == [
        "port backlogs", "diesel prices", "grain futures", "nothing"]
    assert vein_engine._watch_topics("no line") == []


def test_journal_definition_authors_watches(monkeypatch):
    from routers import editor
    calls = []

    async def spy(label, **kw):
        calls.append((label, kw.get("resolve_condition")))
        return "node-1"
    monkeypatch.setattr(editor, "author_watch", spy)

    async def compose(messages, **kw):
        return ("HEADLINE: Signal watch · Port strike\nSUMMARY: S.\n===\n"
                "Body.\n\nWatching: port backlogs, diesel prices")
    monkeypatch.setattr(vein_engine, "_vera", compose)
    _stub_search(monkeypatch, [{"title": "A", "url": "https://a", "content": "x",
                                "published": None}])
    defn = vein_defs.save_custom({
        "kind": "journaled", "label": "Journaled", "icon": "eye", "journal": True,
        "pipeline": [
            {"block": "web_search", "params": {"query": "ports"}},
            {"block": "llm_compose"},
        ],
        "schedule": "0 */6 * * *",
    })
    dry = _run(vein_engine.run_definition(defn, dry_run=True))
    assert dry["ok"] is True and calls == []
    out = _run(vein_engine.run_vein("journaled", manual=True))
    assert out["cards"] == 1
    assert calls == [("Port strike", "port backlogs, diesel prices")]


def _findings():
    return [{"title": "River at 24.1 ft", "content": "over flood stage",
             "severity": "notice",
             "trip_sources": [{"title": "gauge", "url": "https://waterdata.example/g"}]},
            {"title": "Levee overtopping alert", "content": "downstream alert",
             "severity": "alert",
             "trip_sources": [{"title": "alert", "url": "https://alerts.example/l"}]}]


def _stub_cluster(monkeypatch, situations):
    from routers import structured

    async def fake_parsed(call, schema):
        return situations, []
    monkeypatch.setattr(structured, "parsed", fake_parsed)


def test_cluster_merges_members_and_mints_situation_keys(monkeypatch):
    _stub_cluster(monkeypatch, {"situations": [
        {"headline": "River flooding event", "members": [0, 1], "query": "river flood"}]})
    _stub_search(monkeypatch, [{"title": "coverage", "url": "https://press.example/c",
                                "content": "details", "published": None}])
    out = _run(vein_engine._run_situation_cluster(_findings(), {}, CTX))
    assert len(out) == 1
    it = out[0]
    assert it["key"] == "sit:river-flooding-event"
    assert it["title"] == "River flooding event"
    assert it["severity"] == "alert"
    assert [s["url"] for s in it["sources"]] == [
        "https://waterdata.example/g", "https://alerts.example/l", "https://press.example/c"]
    assert [s["n"] for s in it["sources"]] == [1, 2, 3]
    assert "Vetted findings" in it["content"]


def test_cluster_falls_back_to_one_situation_per_finding(monkeypatch):
    from routers import structured

    async def fake_parsed(call, schema):
        return None, ["bad json"]
    monkeypatch.setattr(structured, "parsed", fake_parsed)
    _stub_search(monkeypatch, [])
    out = _run(vein_engine._run_situation_cluster(_findings(), {}, CTX))
    assert len(out) == 2
    assert all(it["key"].startswith("sit:") for it in out)


def test_cluster_deepen_query_param(monkeypatch):
    _stub_cluster(monkeypatch, {"situations": [
        {"headline": "Port strike", "members": [0], "query": "port"}]})
    from types import SimpleNamespace
    from routers import websearch
    queries = []

    async def fake(req):
        queries.append(req.query)
        return SimpleNamespace(results=[])
    monkeypatch.setattr(websearch, "search", fake)
    finding = [{"title": "Port strike", "content": "d", "severity": "alert", "deepen": True}]
    _run(vein_engine._run_situation_cluster(
        finding, {"deepen_query": "which categories are affected"}, CTX))
    assert len(queries) == 2 and "categories" in queries[1]
    queries.clear()
    flat = [{"title": "Port strike", "content": "d", "severity": "alert", "deepen": False}]
    _run(vein_engine._run_situation_cluster(
        flat, {"deepen_query": "which categories are affected"}, CTX))
    assert len(queries) == 1
    queries.clear()
    _run(vein_engine._run_situation_cluster(finding, {}, CTX))
    assert len(queries) == 1


def test_cluster_is_monitor_and_empty_is_empty():
    assert vein_engine.is_monitor([{"block": "situation_cluster"}])
    assert _run(vein_engine._run_situation_cluster([], {}, CTX)) == []


def test_block_modules_load_from_dir(monkeypatch, tmp_path):
    d = tmp_path / "blocks.d"
    d.mkdir()
    (d / "mine.py").write_text(
        "from routers import vein_engine\n"
        "async def _mine(items, params, ctx):\n"
        "    return items\n"
        "vein_engine.register('my_source', _mine)\n", encoding="utf-8")
    (d / "broken.py").write_text("import nope_never\n", encoding="utf-8")
    monkeypatch.setattr(vein_engine, "BLOCKS_DIR", str(d))
    try:
        assert vein_engine.load_block_modules() == ["mine.py"]
        assert "my_source" in vein_engine.BLOCKS
    finally:
        vein_engine.BLOCKS.pop("my_source", None)


def test_standing_definition_keeps_one_card_updated(monkeypatch):
    defn = vein_defs.save_custom({
        "kind": "ticker", "label": "Ticker", "icon": "chart.xyaxis.line", "standing": True,
        "pipeline": [
            {"block": "http_fetch", "params": {"url": "https://feed.example/now.json",
                                               "label": "Reading"}},
            {"block": "llm_compose"},
        ],
        "schedule": "*/30 * * * *",
    })
    assert defn["standing"] is True
    monkeypatch.setattr(vein_engine, "_vera", _fake_vera())
    _stub_get(monkeypatch, 200, "reading one")
    out = _run(vein_engine.run_vein("ticker", manual=True))
    assert out["cards"] == 1
    first = _active("ticker")
    assert len(first) == 1
    again = _run(vein_engine.run_vein("ticker", manual=True))
    assert again == {"ok": True, "situations": 1, "cards": 0, "standing": 1}
    assert [c["id"] for c in _active("ticker")] == [first[0]["id"]]
    _stub_get(monkeypatch, 200, "reading two")
    changed = _run(vein_engine.run_vein("ticker", manual=True))
    assert changed["cards"] == 1
    second = _active("ticker")
    assert len(second) == 1 and second[0]["id"] != first[0]["id"]


def test_compose_prompt_teaches_stats_blocks():
    assert "vera:stats" in vein_engine.COMPOSE_SYS
    assert "vera:chart" in vein_engine.COMPOSE_SYS
