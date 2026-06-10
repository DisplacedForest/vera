"""Pulse async run-status store tests — the persisted single-row run state that lets
/pulse/run return 202 and callers poll to completion. Deterministic; the endpoint wiring (overlap guard,
background task) is verified live via curl. Run: python3 -m pytest tests/test_pulse_run_status.py
"""
import os
import sys
import tempfile
import time

os.environ["PULSE_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "pulse.db")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routers"))
import pulse_store as ps  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate():
    ps.DB_PATH = os.environ["PULSE_DB_PATH"]
    yield


def test_idle_when_never_run():
    assert ps.get_run_status() == {"state": "idle"}


def test_set_get_round_trip():
    d = {"run_id": "r1", "state": "running", "kind": "run", "started_at": int(time.time()),
         "finished_at": None, "topics": [], "injected": [], "errors": []}
    ps.set_run_status(d)
    got = ps.get_run_status()
    assert got["run_id"] == "r1" and got["state"] == "running" and got["kind"] == "run"


def test_finished_run_reports_ok_with_results():
    ps.set_run_status({"run_id": "r2", "state": "ok", "kind": "run", "started_at": 1, "finished_at": 2,
                       "topics": ["A", "B"], "injected": ["A"], "errors": ["B: empty"]})
    got = ps.get_run_status()
    assert got["state"] == "ok" and got["injected"] == ["A"] and got["errors"] == ["B: empty"]


def test_running_overrides_to_stale_when_old():
    old = int(time.time()) - ps.RUN_STALE_SECS - 60
    ps.set_run_status({"run_id": "r3", "state": "running", "kind": "run", "started_at": old,
                       "finished_at": None, "topics": [], "injected": [], "errors": []})
    assert ps.get_run_status()["state"] == "stale"


def test_recent_running_is_not_stale():
    ps.set_run_status({"run_id": "r4", "state": "running", "kind": "run", "started_at": int(time.time()),
                       "finished_at": None, "topics": [], "injected": [], "errors": []})
    assert ps.get_run_status()["state"] == "running"


def test_running_from_before_process_start_is_stale():
    # a run that began before THIS process started == orphaned by a mid-run vera-api restart
    now = int(time.time())
    ps.set_run_status({"run_id": "r6", "state": "running", "kind": "run", "started_at": now - 60,
                       "finished_at": None, "topics": [], "injected": [], "errors": []})
    orig = ps._PROC_START
    try:
        ps._PROC_START = now - 120          # process started before the run -> not a restart -> running
        assert ps.get_run_status()["state"] == "running"
        ps._PROC_START = now                # process started AFTER the run began -> restart -> stale
        assert ps.get_run_status()["state"] == "stale"
    finally:
        ps._PROC_START = orig


def test_finished_running_never_stale():
    # a 'running' row that DID finish (shouldn't happen, but be safe) is not overridden
    old = int(time.time()) - ps.RUN_STALE_SECS - 60
    ps.set_run_status({"run_id": "r5", "state": "ok", "kind": "run", "started_at": old,
                       "finished_at": old + 5, "topics": [], "injected": [], "errors": []})
    assert ps.get_run_status()["state"] == "ok"


if __name__ == "__main__":
    for fn in [test_idle_when_never_run, test_set_get_round_trip, test_finished_run_reports_ok_with_results,
               test_running_overrides_to_stale_when_old, test_recent_running_is_not_stale,
               test_running_from_before_process_start_is_stale, test_finished_running_never_stale]:
        ps.DB_PATH = os.environ["PULSE_DB_PATH"]; fn()
    print("OK")
