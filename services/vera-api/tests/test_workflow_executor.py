import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from routers import pulse  # noqa: E402
from routers import workflow_executor  # noqa: E402
from routers import workflow_store  # noqa: E402


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def stub_types():
    registered = []

    def add(node_type, run=None, pull=None, barrier=False):
        workflow_executor.register(node_type, run=run, pull=pull, barrier=barrier)
        registered.append(node_type)

    yield add
    for node_type in registered:
        workflow_executor.NODE_IMPLS.pop(node_type, None)


def test_generic_graph_runs_in_topological_order_with_item_flow(stub_types):
    ran = []

    async def source(node, items, ctx):
        ran.append(node["id"])
        return [1, 2]

    async def bump(node, items, ctx):
        ran.append(node["id"])
        return [i + 10 for i in items]

    stub_types("g.source", run=source)
    stub_types("g.bump", run=bump)
    definition = {
        "id": "generic",
        "nodes": [{"id": "last", "type": "g.bump"}, {"id": "first", "type": "g.source"},
                  {"id": "middle", "type": "g.bump"}],
        "edges": [{"from": "middle", "to": "last"}, {"from": "first", "to": "middle"}],
    }
    ctx = workflow_executor.RunContext(definition)
    terminal = run(workflow_executor.execute(definition, ctx))
    assert ran == ["first", "middle", "last"]
    assert terminal == [21, 22]


def test_cyclic_graph_is_rejected(stub_types):
    async def noop(node, items, ctx):
        return items

    stub_types("g.noop", run=noop)
    definition = {
        "id": "generic",
        "nodes": [{"id": "a", "type": "g.noop"}, {"id": "b", "type": "g.noop"}],
        "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
    }
    ctx = workflow_executor.RunContext(definition)
    with pytest.raises(ValueError, match="cycle"):
        run(workflow_executor.execute(definition, ctx))


@pytest.fixture
def pulse_harness(monkeypatch, tmp_path):
    monkeypatch.setattr(workflow_store, "DB_PATH", str(tmp_path / "workflows.db"))
    monkeypatch.setattr(pulse.store, "sweep", lambda day: 0)
    monkeypatch.setattr(pulse.up, "get", lambda uid: {"name": "Z", "interests": [], "persona": None})
    monkeypatch.setattr(pulse.vi, "cooled", lambda topics: set())

    journal = {"events": [], "research_kwargs": []}

    async def _no_memories():
        return []

    async def _vision(pause):
        journal["events"].append(("vision", pause))

    async def _phase(pending, errs, items_by_card=None):
        journal["events"].append(("audit", [card["title"] for card, _ in pending]))

    async def fake_triage(who, persona, interests, memories, exclusions, want, rnd):
        return [{"title": "A", "angle": "", "query": "A"}] if rnd == 0 else []

    async def fake_research(t, who, user_id, idx, provenance, errors, defer_audit=False,
                            outcome=None, **kwargs):
        journal["research_kwargs"].append(kwargs)
        journal["events"].append(("research", t["title"]))
        return {"id": f"id-{t['title']}", "title": t["title"], "_corpus": []}

    monkeypatch.setattr(pulse, "_get_memories", _no_memories)
    monkeypatch.setattr(pulse, "_vision", _vision)
    monkeypatch.setattr(pulse, "_audit_phase", _phase)
    monkeypatch.setattr(pulse, "_recent_for_user", lambda uid: [])
    monkeypatch.setattr(pulse, "_triage", fake_triage)
    monkeypatch.setattr(pulse, "research_topic", fake_research)
    monkeypatch.setattr(pulse, "PULSE_MIN_CARDS", 1)
    monkeypatch.setattr(pulse, "PULSE_MAX_CARDS", 5)
    monkeypatch.setattr(pulse, "PULSE_TRIAGE_ROUNDS", 1)
    return journal


def _tracked_run(definition=None):
    version = workflow_store.start_run("pulse", "run-1")
    out = run(pulse._do_run(pulse.PulseRequest(),
                            workflow_definition=definition or version["definition"],
                            workflow_run_id="run-1"))
    return out, workflow_store.latest_run("pulse")


def test_tracked_run_writes_live_node_rows(pulse_harness):
    out, record = _tracked_run()
    assert out["injected"] == ["A"]
    rows = {row["id"]: row for row in record["nodes"]}
    assert set(rows) == {"triage", "gates", "synthesis", "claim_audit", "cover_art", "inject"}
    for row in rows.values():
        assert row["state"] == "ok"
        assert row["started_at"] and row["finished_at"]
        assert row["started_at"] <= row["finished_at"]
        assert row["input"] is not None and row["output"] is not None
    assert rows["triage"]["output"]["proposed"] == 1
    assert rows["gates"]["input"] == {"items": 1}
    assert rows["synthesis"]["output"]["cards"] == 1
    assert rows["claim_audit"]["input"] == {"items": 1}
    assert rows["cover_art"]["output"]["style"] == "rotating"
    assert rows["inject"]["output"]["cards"] == 1


def _with_stub(definition, upstream="synthesis", downstream="claim_audit"):
    definition["nodes"].insert(
        next(i for i, n in enumerate(definition["nodes"]) if n["id"] == downstream),
        {"id": "stub", "type": "stub.transform"})
    definition["edges"] = [e for e in definition["edges"]
                           if e != {"from": upstream, "to": downstream}]
    definition["edges"].extend([{"from": upstream, "to": "stub"},
                                {"from": "stub", "to": downstream}])
    return definition


def test_inserted_optional_node_runs_at_its_position(pulse_harness, stub_types):
    seen = []

    async def stub_run(node, items, ctx):
        seen.append([item.get("title") for item in items])
        ctx.data["out"]["errors"].append("stub saw the cards")
        return items

    stub_types("stub.transform", run=stub_run)
    definition = _with_stub(workflow_store.baseline_definition())
    out, record = _tracked_run(definition)
    assert seen == [["A"]]
    events = pulse_harness["events"]
    assert events.index(("research", "A")) < len(events) - 1
    assert ("audit", ["A"]) in events
    stub_pos = out["errors"].index("stub saw the cards") if "stub saw the cards" in out["errors"] else None
    assert stub_pos is not None
    assert [row["id"] for row in record["nodes"]].index("stub") >= 0
    rows = {row["id"]: row for row in record["nodes"]}
    assert rows["stub"]["state"] == "ok"
    assert rows["stub"]["output"]["items"] == 1


def test_inserted_optional_node_failure_degrades_to_warning(pulse_harness, stub_types):
    async def stub_run(node, items, ctx):
        raise RuntimeError("experimental node exploded")

    stub_types("stub.transform", run=stub_run)
    definition = _with_stub(workflow_store.baseline_definition())
    out, record = _tracked_run(definition)
    assert out["injected"] == ["A"]
    assert ("audit", ["A"]) in pulse_harness["events"]
    rows = {row["id"]: row for row in record["nodes"]}
    assert rows["stub"]["state"] == "warning"
    assert "experimental node exploded" in rows["stub"]["error"]
    assert rows["claim_audit"]["state"] == "ok"


def test_core_stage_failure_fails_the_run(pulse_harness, monkeypatch):
    async def broken_phase(pending, errs, items_by_card=None):
        raise RuntimeError("audit model unreachable")

    monkeypatch.setattr(pulse, "_audit_phase", broken_phase)
    version = workflow_store.start_run("pulse", "run-1")
    with pytest.raises(RuntimeError, match="audit model unreachable"):
        run(pulse._do_run(pulse.PulseRequest(), workflow_definition=version["definition"],
                          workflow_run_id="run-1"))
    record = workflow_store.latest_run("pulse")
    rows = {row["id"]: row for row in record["nodes"]}
    assert rows["claim_audit"]["state"] == "error"
    assert "audit model unreachable" in rows["claim_audit"]["error"]
    assert ("vision", False) in pulse_harness["events"]


def test_run_started_before_promotion_completes_on_its_pinned_version(pulse_harness):
    pinned = workflow_store.start_run("pulse", "run-1")
    draft = workflow_store.create_draft("pulse")
    definition = draft["definition"]
    next(n for n in definition["nodes"] if n["id"] == "cover_art")["config"] = {"style": "photographic"}
    workflow_store.save_draft(draft["id"], definition)
    promoted = workflow_store.promote(draft["id"])
    out = run(pulse._do_run(pulse.PulseRequest(), workflow_definition=pinned["definition"],
                            workflow_run_id="run-1"))
    assert out["injected"] == ["A"]
    assert pulse_harness["research_kwargs"] == [{}]
    record = workflow_store.latest_run("pulse")
    assert record["workflow_version_id"] == pinned["id"]
    assert record["workflow_version_id"] != promoted["id"]


def test_node_implementation_modules_reference_no_owui():
    for name in ("pulse_nodes.py", "workflow_executor.py"):
        path = os.path.join(os.path.dirname(__file__), "..", "routers", name)
        source = open(path).read()
        assert "OWUI_BASE" not in source
        assert "OWUI_KEY" not in source
