"""Plain-English run-detail summaries. Standalone — run: pytest tests/test_scheduler_summary.py

A scheduler run's recorded detail must read as one or two human sentences with the
headline numbers, never a serialized run record. These cover the named job kinds
(pulse, signals, heartbeat, updates) plus the gated and fallback paths, and assert
that no output carries dict/repr markers.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from routers.scheduler import summarize_outcome  # noqa: E402


def _no_record_markers(s: str):
    # A serialized dict/record leaks braces, key quotes, or bracketed lists.
    assert "{" not in s and "}" not in s
    assert "':" not in s and "['" not in s


def test_pulse_summary_has_cards_and_gate_count():
    out = summarize_outcome("pulse", {
        "state": "done",
        "injected": [{"id": "a"}, {"id": "b"}],
        "gates": {"dedup": 6, "freshness": 1},
        "warnings": ["starved run: 2/8 cards after 3 triage rounds"],
    })
    assert "shipped 2 cards" in out
    assert "7 candidates cut by gates" in out
    assert "1 warning" in out
    _no_record_markers(out)


def test_pulse_single_card_is_singular():
    out = summarize_outcome("pulse", {"state": "done", "injected": [{"id": "a"}], "gates": {}})
    assert "shipped 1 card." in out
    _no_record_markers(out)


def test_heartbeat_summary():
    out = summarize_outcome("heartbeat", {
        "ok": True, "learned": ["a"], "refined": True, "proposed": {"verb": "x"},
    })
    assert "learned 1 thing" in out
    assert "refined her instructions" in out
    assert "proposed an action" in out
    _no_record_markers(out)
    idle = summarize_outcome("heartbeat", {"ok": True, "learned": [], "refined": False, "proposed": None})
    assert "nothing to do this cycle" in idle


def test_updates_summary_paths():
    posted = summarize_outcome("updates", {"ok": True, "total": 12, "posted": True})
    assert "Checked 12 components" in posted and "posted an updates card" in posted
    current = summarize_outcome("updates", {"ok": True, "total": 1, "posted": False, "cleared": 0})
    assert "Checked 1 component;" in current and "all current" in current
    cleared = summarize_outcome("updates", {"ok": True, "total": 5, "posted": False, "cleared": 2})
    assert "cleared 2 resolved cards" in cleared


def test_healthcheck_summary():
    up = summarize_outcome("healthcheck", {"ok": True, "down": []})
    assert up == "Service health probe: all services up."
    down = summarize_outcome("healthcheck", {"ok": True, "down": ["voice", "searxng"]})
    assert "2 services down" in down and "voice, searxng" in down


def test_gated_run_is_plain_skip():
    out = summarize_outcome("weather", {"ok": False, "disabled": True, "detail": "the weather vein is off"})
    assert out == "Skipped: the weather vein is off"


def test_generic_fallback_never_dumps_dict():
    out = summarize_outcome("memory_groom", {"ok": True, "pruned": 4, "kept": 99, "nested": {"a": 1}})
    assert out == "Episodic memory groom completed."
    _no_record_markers(out)


def test_non_dict_results_are_safe():
    assert summarize_outcome("weather", "all clear, 3 zones").startswith("all clear")
    assert summarize_outcome("weather", None) == "Completed."


def test_vein_run_quiet_and_posted():
    quiet = summarize_outcome("vein_rivergauge", {"ok": True, "situations": 0, "cards": 0})
    assert quiet == "Vein checked: quiet, nothing cleared the bar."
    posted = summarize_outcome("vein_rivergauge", {"ok": True, "situations": 2, "cards": 2})
    assert "2 situations" in posted and "2 cards" in posted
    _no_record_markers(posted)


def test_vein_run_floor_skip_and_failure():
    skip = summarize_outcome("vein_newswatch", {
        "ok": True, "skipped": "schedule floor",
        "detail": "last run 5m ago; the floor for LLM pipelines is 30m"})
    assert skip.startswith("Skipped:") and "floor" in skip
    fail = summarize_outcome("vein_newswatch", {
        "ok": False, "block": "http_fetch", "detail": "HTTP 500 from https://x"})
    assert "failed at http_fetch" in fail
    _no_record_markers(skip)
