"""Agentic activity feed tests. Standalone — run: pytest tests/test_agentic.py

Covers the merge across heartbeat/action/scheduler stores, the normalized event
shape, newest-first ordering, per-source failure isolation (a missing or broken
store empties its contribution, never the feed), and the OWUI source staying
silent while unconfigured.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from routers import action_store  # noqa: E402
from routers import agentic  # noqa: E402
from routers import heartbeat_store  # noqa: E402
from routers import scheduler_store  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setattr(heartbeat_store, "DB_PATH", str(tmp_path / "heartbeat.db"))
    monkeypatch.setattr(action_store, "DB_PATH", str(tmp_path / "actions.db"))
    monkeypatch.setattr(scheduler_store, "DB_PATH", str(tmp_path / "scheduler.db"))
    monkeypatch.setattr(agentic, "OWUI_BASE", "")
    monkeypatch.setattr(agentic, "OWUI_KEY", "")
    yield


def _activity(hours=24):
    return asyncio.run(agentic.activity(hours=hours))


def test_empty_stores_yield_empty_feed():
    out = _activity()
    assert out == {"hours": 24, "events": []}


def test_missing_stores_never_500(tmp_path):
    # Point every store at a path that does not exist and cannot be created lazily
    # as a db file — the feed must still answer.
    for mod in (heartbeat_store, action_store, scheduler_store):
        setattr(mod, "DB_PATH", str(tmp_path / "nope" / "deeper" / "x.db"))
    out = _activity()
    assert out["events"] == []


def test_heartbeat_events_normalized():
    heartbeat_store.log("learn", "ha.service:climate.office")
    heartbeat_store.log("mystery_kind", "something new")
    events = _activity()["events"]
    assert len(events) == 2
    by_kind = {e["kind"]: e for e in events}
    learn = by_kind["learn"]
    assert learn["source"] == "heartbeat"
    assert learn["title"] == "Studied the house"
    assert learn["detail"] == "ha.service:climate.office"
    # Unknown kinds fall back to the kind itself, never crash.
    assert by_kind["mystery_kind"]["title"] == "mystery_kind"


def test_action_events_carry_lane_and_attribution():
    token = action_store.stage("ha.service", {"domain": "light", "service": "turn_off"},
                               "Turn off the lights", "low", True)
    action_store.set_result(token, {"ok": True})
    action_store.log_auto("media.request", {"title": "Dune"}, {"ok": True})
    events = _activity()["events"]
    assert len(events) == 2
    by_tool = {e["tool"]: e for e in events}
    gated = by_tool["ha.service"]
    assert (gated["source"], gated["kind"], gated["ref"]) == ("action", "gated", token)
    assert gated["title"] == "ha.service"
    assert gated["detail"].startswith("applied")
    auto = by_tool["media.request"]
    assert (auto["kind"], auto["ref"]) == ("auto", None)


def test_scheduler_events_use_registry_labels():
    scheduler_store.record_outcome("heartbeat", True, "tick ok")
    scheduler_store.record_outcome("not_a_job", False, "boom")
    events = _activity()["events"]
    assert len(events) == 2
    by_tool = {e["tool"]: e for e in events}
    ok = by_tool["heartbeat"]
    assert (ok["source"], ok["kind"], ok["title"]) == ("scheduler", "ok", "Heartbeat tick")
    # Unregistered job ids label as themselves.
    assert (by_tool["not_a_job"]["kind"], by_tool["not_a_job"]["title"]) == ("fail", "not_a_job")


def test_run_log_accumulates_history():
    scheduler_store.record_outcome("heartbeat", True, "first")
    scheduler_store.record_outcome("heartbeat", True, "second")
    runs = scheduler_store.recent_runs(24)
    assert [r["detail"] for r in runs] == ["second", "first"]
    # The snapshot still reflects only the latest run.
    assert scheduler_store.overrides()["heartbeat"]["last_detail"] == "second"


def test_merged_feed_is_newest_first():
    heartbeat_store.log("learn", "old")
    scheduler_store.record_outcome("heartbeat", True, "new")
    events = _activity()["events"]
    assert [e["ts"] for e in events] == sorted([e["ts"] for e in events], reverse=True)


def test_window_filters_old_events():
    import time
    with heartbeat_store._conn() as c:
        heartbeat_store.init()
        c.execute("INSERT INTO outcome(ts,kind,detail,extra) VALUES(?,?,?,?)",
                  (int(time.time()) - 48 * 3600, "learn", "two days ago", None))
    heartbeat_store.log("learn", "now")
    events = _activity(hours=24)["events"]
    assert [e["detail"] for e in events] == ["now"]
    assert len(_activity(hours=72)["events"]) == 2


def test_broken_source_does_not_empty_feed(monkeypatch):
    heartbeat_store.log("learn", "fine")
    monkeypatch.setattr(agentic, "_action_events",
                        lambda hours: (_ for _ in ()).throw(RuntimeError("db corrupt")))
    events = _activity()["events"]
    assert [e["source"] for e in events] == ["heartbeat"]


def test_owui_source_silent_when_unconfigured():
    assert asyncio.run(agentic._owui_events(24, 0)) == []
