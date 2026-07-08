"""Agentic canvas graph tests. Standalone — run: pytest tests/test_agentic_graph.py

Covers the manifest contract the Mac app renders from: every registry job appears
as a flow with a complete face, every feed targets a declared surface, drill-in
stages ride the manifest, pulse stage state distills the structured run status,
heartbeat branch state reads the outcome log, surface stats answer live and
degrade to None (never a 500) when a backing store can't answer.
"""
import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from routers import action_store  # noqa: E402
from routers import agentic  # noqa: E402
from routers import heartbeat_store  # noqa: E402
from routers import pulse_store  # noqa: E402
from routers import scheduler_store  # noqa: E402
from routers import vera_memory_store  # noqa: E402
from routers.scheduler import REGISTRY  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setattr(heartbeat_store, "DB_PATH", str(tmp_path / "heartbeat.db"))
    monkeypatch.setattr(action_store, "DB_PATH", str(tmp_path / "actions.db"))
    monkeypatch.setattr(scheduler_store, "DB_PATH", str(tmp_path / "scheduler.db"))
    monkeypatch.setattr(pulse_store, "DB_PATH", str(tmp_path / "pulse.db"))
    monkeypatch.setattr(vera_memory_store, "DB_PATH", str(tmp_path / "vera_memory.db"))
    yield


def _graph():
    return asyncio.run(agentic.graph())


def _flow(out, flow_id):
    return next(f for f in out["flows"] if f["id"] == flow_id)


def test_every_registry_job_is_a_flow():
    out = _graph()
    assert {f["id"] for f in out["flows"]} == set(REGISTRY)
    for f in out["flows"]:
        for key in ("label", "title", "kind", "icon", "tint", "group", "feeds", "tools", "running"):
            assert key in f, f"flow {f['id']} missing {key}"
        assert f["label"]
        assert f["running"] is False


def test_every_registry_job_has_an_authored_face():
    # A new job must get a deliberate canvas face, not the fallback.
    assert set(agentic.FLOW_FACE) == set(REGISTRY)


def test_vein_jobs_get_faces_from_their_definitions(vein_shapes, monkeypatch, tmp_path):
    from routers import vein_store
    monkeypatch.setattr(vein_store, "PATH", str(tmp_path / "veins.json"))
    out = _graph()
    flow = _flow(out, "vein_weather")
    assert flow["label"] == "Weather"
    assert flow["icon"] == "cloud.sun"
    assert flow["group"] == "Ambient" and flow["feeds"] == ["veins"]


def test_feeds_reference_declared_surfaces():
    out = _graph()
    surface_ids = {s["id"] for s in out["surfaces"]}
    assert surface_ids == {"pulse_feed", "veins", "memory", "actions"}
    for f in out["flows"]:
        assert set(f["feeds"]) <= surface_ids, f"flow {f['id']} feeds unknown surface"
        for stage in f.get("stages") or []:
            assert set(stage.get("feeds") or []) <= surface_ids


def test_explicit_edges_mirror_feeds():
    out = _graph()
    derived = {(f["id"], sid) for f in out["flows"] for sid in f["feeds"]}
    assert {(e["from"], e["to"]) for e in out["edges"]} == derived
    assert derived, "the canvas would be edgeless"


def test_drill_in_topology():
    out = _graph()
    pulse = _flow(out, "pulse")
    assert pulse["stage_layout"] == "pipeline"
    assert [s["id"] for s in pulse["stages"]] == [
        "triage", "gates", "synthesis", "claim_audit", "cover_art", "inject"]
    hb = _flow(out, "heartbeat")
    assert hb["kind"] == "heartbeat"
    assert hb["stage_layout"] == "fan"
    assert [s["id"] for s in hb["stages"]] == ["learn", "refine", "propose", "watch", "foryou"]
    # Simple jobs carry no stages: the manifest decides depth.
    assert "stages" not in _flow(out, "memory_groom")


def test_pulse_stage_state_idle_is_none():
    assert _flow(_graph(), "pulse")["stage_state"] is None


def test_pulse_stage_state_distills_run_status():
    pulse_store.set_run_status({
        "run_id": "1", "state": "ok", "kind": "run",
        "started_at": int(time.time()) - 60, "finished_at": int(time.time()),
        "topics": [], "injected": ["a", "b", "c"],
        "errors": ["starved run: 3/8 cards after 2 triage round(s)", "some other error"],
        "gates": {"dedup": 6, "freshness": 1, "coherence": 1, "empty": 0, "interest_cap": 1},
        "rounds": [{"proposed": ["t1", "t2", "t3"]}, {"proposed": ["t4"]}],
    })
    st = _flow(_graph(), "pulse")["stage_state"]
    assert st["state"] == "ok"
    assert st["rounds"] == 2
    assert st["proposed"] == 4
    assert st["injected"] == 3
    assert st["gates"]["dedup"] == 6
    assert st["warnings"] == ["starved run: 3/8 cards after 2 triage round(s)"]


def test_pulse_running_lifts_flow_running():
    pulse_store.set_run_status({
        "run_id": "2", "state": "running", "kind": "run",
        "started_at": int(time.time()), "finished_at": None,
        "topics": [], "injected": [], "errors": []})
    assert _flow(_graph(), "pulse")["running"] is True


def test_heartbeat_branch_state_latest_per_branch():
    heartbeat_store.log("learn", "old topic")
    heartbeat_store.log("learn", "new topic")
    heartbeat_store.log("foryou_skip", "considered, skipped")
    heartbeat_store.log("confirmed", "ha.service:climate.office")
    bs = _flow(_graph(), "heartbeat")["branch_state"]
    assert bs["learn"]["detail"] == "new topic"
    assert bs["foryou"]["kind"] == "foryou_skip"
    assert bs["propose"]["kind"] == "confirmed"
    assert "refine" not in bs  # never fired: no state, never fake data


def test_surface_stats_live():
    from routers.scheduler import TZ
    from datetime import datetime
    today = datetime.now(TZ).date().isoformat()
    pulse_store.insert_card({"id": "c1", "day": today, "title": "t"})
    pulse_store.insert_card({"id": "c2", "day": "2000-01-01", "title": "old"})
    pulse_store.insert_card({"id": "c3", "day": "2000-01-01", "title": "vein", "kind": "weather"})
    action_store.stage("ha.service", {"x": 1}, "preview", "low", True)
    stats = {s["id"]: s["stat"] for s in _graph()["surfaces"]}
    assert stats["pulse_feed"] == "1 card today"
    assert stats["veins"] == "1 active card"
    assert stats["memory"] == "0 core facts"
    assert stats["actions"] == "1 pending proposal"


def test_missing_stores_degrade_to_none_stats():
    for mod in (pulse_store, action_store, vera_memory_store, heartbeat_store):
        setattr(mod, "DB_PATH", "/dev/null/nope/x.db")
    out = _graph()
    assert {f["id"] for f in out["flows"]} == set(REGISTRY)  # topology survives
    assert all(s["stat"] is None for s in out["surfaces"])
    assert _flow(out, "heartbeat")["branch_state"] == {}
