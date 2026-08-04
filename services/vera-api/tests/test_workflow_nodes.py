import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from routers import pulse  # noqa: E402
from routers import pulse_store  # noqa: E402
from routers import vein_engine  # noqa: E402
from routers import workflow_executor  # noqa: E402
from routers import workflow_nodes  # noqa: E402
from routers import workflow_registry  # noqa: E402
from routers import workflow_store  # noqa: E402


FLOW_TYPES = {"flow.filter": "transform", "flow.llm_step": "transform",
              "flow.http_fetch": "enrich", "flow.notify": "notify"}


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _ctx(data=None):
    return workflow_executor.RunContext({"id": "generic", "nodes": [], "edges": []}, data=data)


def _invoke(node_type, config, items, ctx=None):
    ctx = ctx or _ctx()
    impl = workflow_executor.NODE_IMPLS[node_type]
    node = {"id": "x", "type": node_type, "config": config}
    return run(impl["run"](node, items, ctx)), ctx


def test_catalog_serves_each_flow_node():
    entries = {entry["type"]: entry for entry in workflow_registry.catalog()}
    for node_type, category in FLOW_TYPES.items():
        entry = entries[node_type]
        assert entry["insertable"] is True
        assert entry["category"] == category
        assert entry["config_schema"]
        assert entry["label"]


def test_each_flow_node_is_registered_with_the_executor():
    for node_type in FLOW_TYPES:
        assert node_type in workflow_executor.NODE_IMPLS


def _inserted(node_type, upstream, downstream):
    definition = workflow_store.baseline_definition()
    definition["nodes"].insert(
        next(i for i, n in enumerate(definition["nodes"]) if n["id"] == downstream),
        {"id": "x", "type": node_type})
    definition["edges"] = [e for e in definition["edges"]
                           if e != {"from": upstream, "to": downstream}]
    definition["edges"].extend([{"from": upstream, "to": "x"},
                                {"from": "x", "to": downstream}])
    return definition


SPINE = ["triage", "gates", "synthesis", "claim_audit", "cover_art", "inject"]


@pytest.mark.parametrize("node_type", sorted(FLOW_TYPES))
@pytest.mark.parametrize("gap", list(zip(SPINE, SPINE[1:])))
def test_flow_node_inserts_between_each_adjacent_pulse_stage(node_type, gap):
    workflow_registry.validate_definition("pulse", _inserted(node_type, *gap))


@pytest.mark.parametrize("node_type", sorted(FLOW_TYPES))
def test_flow_node_is_valid_in_a_generic_workflow(node_type):
    workflow_registry.validate_definition("generic", {
        "id": "custom", "nodes": [{"id": "only", "type": node_type}], "edges": []})


def test_filter_config_rejects_unknown_operator():
    definition = _inserted("flow.filter", "gates", "synthesis")
    definition["nodes"][2]["config"] = {"operator": "regex"}
    with pytest.raises(ValueError, match="operator"):
        workflow_registry.validate_definition("pulse", definition)


def test_text_config_field_rejects_non_string():
    definition = _inserted("flow.llm_step", "gates", "synthesis")
    definition["nodes"][2]["config"] = {"prompt": 7}
    with pytest.raises(ValueError, match="prompt must be text"):
        workflow_registry.validate_definition("pulse", definition)


CARDS = [{"title": "Frost warning", "summary": "cold"},
         {"title": "Quiet day", "summary": "calm"},
         {"title": "River rising", "summary": ""}]


def test_filter_drop_removes_matching_cards():
    out, _ = _invoke("flow.filter", {"operator": "contains", "value": "frost", "action": "drop"},
                     list(CARDS))
    assert [c["title"] for c in out] == ["Quiet day", "River rising"]


def test_filter_keep_retains_only_matching_cards():
    out, _ = _invoke("flow.filter", {"field": "summary", "operator": "equals",
                                     "value": "calm", "action": "keep"}, list(CARDS))
    assert [c["title"] for c in out] == ["Quiet day"]


def test_filter_present_and_missing_ignore_value():
    out, _ = _invoke("flow.filter", {"field": "summary", "operator": "missing", "action": "keep"},
                     list(CARDS))
    assert [c["title"] for c in out] == ["River rising"]
    out, _ = _invoke("flow.filter", {"field": "summary", "operator": "present", "action": "keep"},
                     list(CARDS))
    assert [c["title"] for c in out] == ["Frost warning", "Quiet day"]


def test_filter_records_kept_and_dropped_counts():
    _, ctx = _invoke("flow.filter", {"operator": "contains", "value": "river", "action": "keep"},
                     list(CARDS))
    assert ctx.summaries["x"] == {"kept": 1, "dropped": 2}


@pytest.fixture
def fake_vera(monkeypatch):
    calls = {"messages": [], "reply": "NOTE"}

    async def _vera(messages, temperature=0.4, think=None):
        calls["messages"].append(messages)
        return calls["reply"]

    monkeypatch.setattr(pulse, "_vera", _vera)
    return calls


def test_llm_step_requires_a_prompt(fake_vera):
    with pytest.raises(ValueError, match="prompt"):
        _invoke("flow.llm_step", {}, list(CARDS))
    assert fake_vera["messages"] == []


def test_llm_step_annotates_each_card(fake_vera):
    out, _ = _invoke("flow.llm_step", {"prompt": "Rate the urgency."}, list(CARDS))
    assert [c["annotation"] for c in out] == ["NOTE", "NOTE", "NOTE"]
    assert len(fake_vera["messages"]) == 3
    assert fake_vera["messages"][0][0] == {"role": "system", "content": "Rate the urgency."}
    assert "Frost warning" in fake_vera["messages"][0][1]["content"]


def test_llm_step_replaces_summaries(fake_vera):
    out, _ = _invoke("flow.llm_step", {"prompt": "Summarize.", "output": "replace_summary"},
                     list(CARDS))
    assert all(c["summary"] == "NOTE" for c in out)


def test_llm_step_drops_cards_on_empty_reply(fake_vera):
    fake_vera["reply"] = "  "
    out, _ = _invoke("flow.llm_step", {"prompt": "Keep?", "output": "drop_on_empty"}, list(CARDS))
    assert out == []


def test_llm_step_set_mode_makes_one_call_over_the_whole_set(fake_vera):
    out, _ = _invoke("flow.llm_step", {"prompt": "Digest.", "mode": "set"}, list(CARDS))
    assert len(fake_vera["messages"]) == 1
    payload = json.loads(fake_vera["messages"][0][1]["content"])
    assert len(payload["cards"]) == 3
    assert all(c["annotation"] == "NOTE" for c in out)


def test_llm_step_includes_fetched_context(fake_vera):
    ctx = _ctx(data={"fetched": {"gust": 42.0}})
    _invoke("flow.llm_step", {"prompt": "Use the wind."}, [CARDS[0]], ctx=ctx)
    payload = json.loads(fake_vera["messages"][0][1]["content"])
    assert payload["context"] == {"gust": 42.0}


def test_llm_step_with_no_items_never_calls_the_model(fake_vera):
    out, _ = _invoke("flow.llm_step", {"prompt": "Anything."}, [])
    assert out == []
    assert fake_vera["messages"] == []


@pytest.fixture
def fake_get(monkeypatch):
    responses = {"status": 200, "text": json.dumps({"gust": 12})}

    async def _get(url):
        responses["url"] = url
        return responses["status"], responses["text"]

    monkeypatch.setattr(vein_engine, "_get", _get)
    return responses


def test_http_fetch_requires_a_url(fake_get):
    with pytest.raises(ValueError, match="URL"):
        _invoke("flow.http_fetch", {}, list(CARDS))
    assert "url" not in fake_get


def test_http_fetch_merges_extracted_value_into_run_context(fake_get):
    out, ctx = _invoke("flow.http_fetch",
                       {"url": "https://feed.example/data.json", "extract": "gust",
                        "context_key": "gust"}, list(CARDS))
    assert ctx.data["fetched"] == {"gust": 12.0}
    assert out == CARDS
    assert fake_get["url"] == "https://feed.example/data.json"


def test_http_fetch_without_extract_merges_body_text(fake_get):
    fake_get["text"] = "plain body"
    _, ctx = _invoke("flow.http_fetch", {"url": "https://feed.example/page"}, [])
    assert ctx.data["fetched"] == {"fetched": "plain body"}


def test_http_fetch_failure_raises(fake_get):
    fake_get["status"] = 500
    with pytest.raises(vein_engine.BlockError, match="HTTP 500"):
        _invoke("flow.http_fetch", {"url": "https://feed.example/data.json"}, list(CARDS))


@pytest.fixture
def card_store(monkeypatch, tmp_path):
    monkeypatch.setattr(pulse_store, "DB_PATH", str(tmp_path / "pulse.db"))
    return pulse_store


def _notify_cards(store):
    return [c for c in store.list_cards(include_expired=True) if c.get("kind") == "notify"]


def _seed_card(card_id, title, summary=""):
    pulse_store.insert_card({"id": card_id, "day": "2026-01-01", "title": title, "summary": summary})
    return pulse_store.get_card(card_id)


def test_notify_emits_exactly_one_marked_card(card_store):
    out, ctx = _invoke("flow.notify", {}, list(CARDS))
    assert out == CARDS
    cards = _notify_cards(card_store)
    assert len(cards) == 1
    assert cards[0]["situation_key"] == "workflow-notify:generic:x"
    assert cards[0]["severity"] == "notice"
    assert "3 cards reached this step" in cards[0]["summary"]
    assert "Frost warning" in cards[0]["body"]
    assert ctx.summaries["x"] == {"cards": 3}


def test_notify_reruns_never_stack_duplicates(card_store):
    _invoke("flow.notify", {}, list(CARDS))
    _invoke("flow.notify", {"headline": "Evening pass"}, [CARDS[0]])
    cards = _notify_cards(card_store)
    assert len(cards) == 1
    assert cards[0]["title"] == "Evening pass"
    assert "1 card reached this step" in cards[0]["summary"]


def test_notify_rerun_replaces_even_a_bookmarked_card(card_store):
    _invoke("flow.notify", {}, list(CARDS))
    pulse_store.set_status(_notify_cards(card_store)[0]["id"], "bookmarked")
    _invoke("flow.notify", {}, list(CARDS))
    cards = _notify_cards(card_store)
    assert len(cards) == 1
    assert cards[0]["status"] == "new"


def test_notify_scopes_cards_and_keys_per_user(card_store):
    _invoke("flow.notify", {}, list(CARDS), ctx=_ctx(data={"user_id": "alice"}))
    _invoke("flow.notify", {}, [CARDS[0]], ctx=_ctx(data={"user_id": "bob"}))
    _invoke("flow.notify", {}, list(CARDS), ctx=_ctx(data={"user_id": "alice"}))
    cards = _notify_cards(card_store)
    assert len(cards) == 2
    by_user = {c["user_id"]: c for c in cards}
    assert by_user["alice"]["situation_key"] == "workflow-notify:generic:x:alice"
    assert by_user["bob"]["situation_key"] == "workflow-notify:generic:x:bob"


def test_filter_drop_deletes_the_persisted_card(card_store):
    _seed_card("c1", "Frost warning")
    _seed_card("c2", "Quiet day")
    items = [{"id": "c1", "title": "Frost warning"}, {"id": "c2", "title": "Quiet day"}]
    out, _ = _invoke("flow.filter", {"operator": "contains", "value": "frost", "action": "drop"},
                     items)
    assert [c["id"] for c in out] == ["c2"]
    assert pulse_store.get_card("c1") is None
    assert pulse_store.get_card("c2") is not None


def test_llm_step_replace_summary_updates_the_persisted_card(card_store, fake_vera):
    _seed_card("c1", "Frost warning", summary="old")
    _invoke("flow.llm_step", {"prompt": "Summarize.", "output": "replace_summary"},
            [{"id": "c1", "title": "Frost warning", "summary": "old"}])
    assert pulse_store.get_card("c1")["summary"] == "NOTE"


def test_llm_step_drop_on_empty_deletes_the_persisted_card(card_store, fake_vera):
    fake_vera["reply"] = ""
    _seed_card("c1", "Frost warning")
    out, _ = _invoke("flow.llm_step", {"prompt": "Keep?", "output": "drop_on_empty"},
                     [{"id": "c1", "title": "Frost warning"}])
    assert out == []
    assert pulse_store.get_card("c1") is None


@pytest.fixture
def pulse_harness(monkeypatch, tmp_path):
    monkeypatch.setattr(workflow_store, "DB_PATH", str(tmp_path / "workflows.db"))
    monkeypatch.setattr(pulse_store, "DB_PATH", str(tmp_path / "pulse.db"))
    monkeypatch.setattr(pulse.up, "get", lambda uid: {"name": "Z", "interests": [], "persona": None})
    monkeypatch.setattr(pulse.vi, "cooled", lambda topics: set())

    async def _no_memories():
        return []

    async def _vision(pause):
        return None

    async def _phase(pending, errs, items_by_card=None):
        return None

    async def fake_triage(who, persona, interests, memories, exclusions, want, rnd):
        if rnd:
            return []
        return [{"title": "Frost watch", "angle": "", "query": "frost"},
                {"title": "Filler topic", "angle": "", "query": "filler"}]

    async def fake_research(t, who, user_id, idx, provenance, errors, defer_audit=False,
                            outcome=None, **kwargs):
        card = {"id": f"id-{t['title']}", "day": "2026-01-01", "title": t["title"],
                "kind": "research"}
        pulse_store.insert_card(card)
        return {**card, "_corpus": []}

    async def _vera(messages, temperature=0.4, think=None):
        return "NOTE"

    monkeypatch.setattr(pulse, "_get_memories", _no_memories)
    monkeypatch.setattr(pulse, "_vision", _vision)
    monkeypatch.setattr(pulse, "_audit_phase", _phase)
    monkeypatch.setattr(pulse, "_recent_for_user", lambda uid: [])
    monkeypatch.setattr(pulse, "_triage", fake_triage)
    monkeypatch.setattr(pulse, "research_topic", fake_research)
    monkeypatch.setattr(pulse, "_vera", _vera)
    monkeypatch.setattr(pulse, "PULSE_MIN_CARDS", 1)
    monkeypatch.setattr(pulse, "PULSE_MAX_CARDS", 5)
    monkeypatch.setattr(pulse, "PULSE_TRIAGE_ROUNDS", 1)


def _definition_with_all_flow_nodes(fetch_config):
    definition = workflow_store.baseline_definition()
    inserts = [("fetch", "flow.http_fetch", fetch_config, "triage", "gates"),
               ("filter", "flow.filter",
                {"operator": "contains", "value": "filler", "action": "drop"},
                "gates", "synthesis"),
               ("llm", "flow.llm_step", {"prompt": "Annotate."}, "synthesis", "claim_audit"),
               ("notify", "flow.notify", {}, "cover_art", "inject")]
    for node_id, node_type, config, upstream, downstream in inserts:
        definition["nodes"].insert(
            next(i for i, n in enumerate(definition["nodes"]) if n["id"] == downstream),
            {"id": node_id, "type": node_type, "config": config})
        definition["edges"] = [e for e in definition["edges"]
                               if e != {"from": upstream, "to": downstream}]
        definition["edges"].extend([{"from": upstream, "to": node_id},
                                    {"from": node_id, "to": downstream}])
    return definition


def _tracked_run(definition):
    workflow_store.start_run("pulse", "run-1")
    out = run(pulse._do_run(pulse.PulseRequest(), workflow_definition=definition,
                            workflow_run_id="run-1"))
    return out, workflow_store.latest_run("pulse")


def test_pulse_run_executes_every_inserted_flow_node(pulse_harness, fake_get):
    definition = _definition_with_all_flow_nodes(
        {"url": "https://feed.example/data.json", "extract": "gust", "context_key": "gust"})
    workflow_registry.validate_definition("pulse", definition)
    out, record = _tracked_run(definition)
    assert out["injected"] == ["Frost watch"]
    assert "Filler topic" not in out["injected"]
    rows = {row["id"]: row for row in record["nodes"]}
    assert rows["fetch"]["state"] == "ok"
    assert rows["fetch"]["output"]["context_key"] == "gust"
    assert rows["filter"]["state"] == "ok"
    assert rows["filter"]["output"]["dropped"] == 1
    assert rows["llm"]["state"] == "ok"
    assert rows["notify"]["state"] == "ok"
    assert rows["notify"]["output"]["cards"] == 1
    cards = _notify_cards(pulse_store)
    assert len(cards) == 1
    assert cards[0]["situation_key"] == f"workflow-notify:pulse:notify:{pulse_store.DEFAULT_USER}"


def _insert_into(definition, node, upstream, downstream):
    definition["nodes"].insert(
        next(i for i, n in enumerate(definition["nodes"]) if n["id"] == downstream), node)
    definition["edges"] = [e for e in definition["edges"]
                           if e != {"from": upstream, "to": downstream}]
    definition["edges"].extend([{"from": upstream, "to": node["id"]},
                                {"from": node["id"], "to": downstream}])
    return definition


def test_post_synthesis_filter_drop_updates_injected_and_backfills(pulse_harness):
    definition = _insert_into(
        workflow_store.baseline_definition(),
        {"id": "filter", "type": "flow.filter",
         "config": {"operator": "contains", "value": "frost", "action": "drop"}},
        "synthesis", "claim_audit")
    workflow_store.start_run("pulse", "run-1")
    out = run(pulse._do_run(pulse.PulseRequest(max_cards=1), workflow_definition=definition,
                            workflow_run_id="run-1"))
    assert out["injected"] == ["Filler topic"]
    assert pulse_store.get_card("id-Frost watch") is None
    assert pulse_store.get_card("id-Filler topic") is not None
    assert not any("under floor" in e for e in out["errors"])
    dropped = next(i for i in out["items"] if i["title"] == "Frost watch")
    assert dropped["status"] == "dropped"


def test_post_synthesis_llm_drop_updates_injected_and_reports_the_floor(pulse_harness,
                                                                        monkeypatch):
    async def empty_vera(messages, temperature=0.4, think=None):
        return ""

    monkeypatch.setattr(pulse, "_vera", empty_vera)
    definition = _insert_into(
        workflow_store.baseline_definition(),
        {"id": "llm", "type": "flow.llm_step",
         "config": {"prompt": "Keep?", "output": "drop_on_empty"}},
        "claim_audit", "cover_art")
    workflow_store.start_run("pulse", "run-1")
    out = run(pulse._do_run(pulse.PulseRequest(), workflow_definition=definition,
                            workflow_run_id="run-1"))
    assert out["injected"] == []
    assert pulse_store.get_card("id-Frost watch") is None
    assert pulse_store.get_card("id-Filler topic") is None
    assert any("under floor" in e for e in out["errors"])
    assert all(i["status"] == "dropped" for i in out["items"])


def test_barrier_node_before_synthesis_cannot_exceed_the_card_target(pulse_harness):
    definition = workflow_store.baseline_definition()
    definition["nodes"].insert(
        next(i for i, n in enumerate(definition["nodes"]) if n["id"] == "synthesis"),
        {"id": "llm", "type": "flow.llm_step", "config": {"prompt": "Annotate."}})
    definition["edges"] = [e for e in definition["edges"]
                           if e != {"from": "gates", "to": "synthesis"}]
    definition["edges"].extend([{"from": "gates", "to": "llm"},
                                {"from": "llm", "to": "synthesis"}])
    workflow_store.start_run("pulse", "run-1")
    out = run(pulse._do_run(pulse.PulseRequest(max_cards=1), workflow_definition=definition,
                            workflow_run_id="run-1"))
    assert out["injected"] == ["Frost watch"]
    assert "Filler topic" in out["skipped"]
    assert out["gates"]["target_cap"] == 1
    record = workflow_store.latest_run("pulse")
    rows = {row["id"]: row for row in record["nodes"]}
    assert rows["llm"]["state"] == "ok"
    assert rows["synthesis"]["output"]["cards"] == 1


def test_unconfigured_http_fetch_degrades_to_warning_and_passes_through(pulse_harness):
    definition = _definition_with_all_flow_nodes({})
    out, record = _tracked_run(definition)
    assert out["injected"] == ["Frost watch"]
    rows = {row["id"]: row for row in record["nodes"]}
    assert rows["fetch"]["state"] == "warning"
    assert "URL" in rows["fetch"]["error"]
    assert rows["gates"]["state"] == "ok"
    assert rows["notify"]["state"] == "ok"
