"""Reconciler unit tests. Standalone — run: python3 tests/test_home_reconcile.py

These exercise the deps-free core: live-state matchers, index classification (the 'idle ≠ fault'
guarantee), the entity baseline diff with flap debounce, the drift ledger's dedup/resolve lifecycle,
and the last_verified staleness signal. No FastAPI / aiohttp / network needed.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routers"))

import home_reconcile_match as mm  # noqa: E402
import home_reconcile_store as store  # noqa: E402
import knowledge_store as ks  # noqa: E402


def st(eid, friendly=None):
    return {"entity_id": eid, "attributes": {"friendly_name": friendly or eid}}


def _fresh_store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    store.DB_PATH = path
    store.init()


def _fresh_ks():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    ks.DB_PATH = path
    ks.init()


# ---- matchers --------------------------------------------------------------

def test_match_entity_glob_name():
    states = [st("climate.home", "Home"), st("climate.bedroom", "Bedroom"),
              st("sensor.unraid_cpu", "Unraid CPU"), st("sensor.rack_gpu", "GPU Power")]
    assert mm.match_entities(states, {"by": "entity", "value": "climate.home"}) == ["climate.home"]
    assert mm.match_entities(states, {"by": "entity", "value": "climate.attic"}) == []
    assert mm.match_entities(states, {"by": "glob", "value": "climate.*"}) == ["climate.home", "climate.bedroom"]
    # name_contains hits entity_id ("unraid") AND friendly_name ("Unraid CPU")
    assert mm.match_entities(states, {"by": "name_contains", "value": "unraid"}) == ["sensor.unraid_cpu"]
    print("ok test_match_entity_glob_name")


def test_classify_index_episodic_idle_not_fault():
    # episodic source with zero live entities is IDLE, never unresolved/failed
    assert mm.classify_index(0, "episodic", 0, can_succeed=False) == "idle"
    assert mm.classify_index(1, "episodic", 0, can_succeed=False) == "active"
    # continuous: 0 -> auto_resolve when a successor exists, else unresolved
    assert mm.classify_index(0, "continuous", 1, can_succeed=True) == "auto_resolve"
    assert mm.classify_index(0, "continuous", 1, can_succeed=False) == "unresolved"
    assert mm.classify_index(2, "continuous", 5, can_succeed=False) == "degraded"
    assert mm.classify_index(9, "continuous", 5, can_succeed=False) == "ok"
    print("ok test_classify_index_episodic_idle_not_fault")


def test_find_successor():
    # a pinned sensor renamed to a _2 collision -> single obvious successor
    states = [st("sensor.dishwasher_power_2", "Dishwasher Power"), st("light.kitchen", "Kitchen")]
    assert mm.find_successor("sensor.dishwasher_power", states) == "sensor.dishwasher_power_2"
    # ambiguity -> no auto-resolve
    states2 = [st("sensor.dishwasher_power_2"), st("sensor.dishwasher_power_3")]
    assert mm.find_successor("sensor.dishwasher_power", states2) is None
    # nothing comparable -> None
    assert mm.find_successor("climate.home", [st("climate.bedroom")]) is None
    print("ok test_find_successor")


# ---- baseline diff (pure) --------------------------------------------------

def test_diff_new_removed_debounce():
    seen = {"light.a": 0, "light.b": 0, "lock.c": 0}
    # b disappears (1 miss, under threshold 3 -> not removed yet), d is new
    new, removed, nxt = store._diff(seen, {"light.a", "lock.c", "fan.d"}, threshold=3)
    assert new == ["fan.d"], new
    assert removed == [], removed
    assert nxt["light.b"] == 1 and nxt["light.a"] == 0 and nxt["fan.d"] == 0, nxt
    # b keeps missing until it crosses the threshold
    new, removed, nxt = store._diff({"light.b": 2}, set(), threshold=3)
    assert removed == ["light.b"] and "light.b" not in nxt, (removed, nxt)
    print("ok test_diff_new_removed_debounce")


def test_apply_diff_first_run_silent_then_detects():
    _fresh_store()
    meta = {"climate.home": {"domain": "climate", "integration": None, "friendly_name": "Home"},
            "lock.front": {"domain": "lock", "integration": None, "friendly_name": "Front"}}
    r = store.apply_diff(meta, threshold=2)
    assert r["first_run"] and r["new"] == [] and r["removed"] == [], r  # silent seed
    # add a new lock -> shows as new; nothing removed
    meta2 = dict(meta, **{"lock.back": {"domain": "lock", "integration": None, "friendly_name": "Back"}})
    r = store.apply_diff(meta2, threshold=2)
    assert [e["entity_id"] for e in r["new"]] == ["lock.back"], r
    assert r["removed"] == [], r
    # drop lock.front: miss 1 (threshold 2) -> not yet; second drop -> removed
    r = store.apply_diff(meta2 := {k: v for k, v in meta2.items() if k != "lock.front"}, threshold=2)
    assert r["removed"] == [], r
    r = store.apply_diff(meta2, threshold=2)
    assert [e["entity_id"] for e in r["removed"]] == ["lock.front"], r
    print("ok test_apply_diff_first_run_silent_then_detects")


def test_preview_diff_is_readonly():
    _fresh_store()
    meta = {"light.a": {"domain": "light", "integration": None, "friendly_name": "A"}}
    store.apply_diff(meta, threshold=2)  # seed
    meta2 = {"light.a": {"domain": "light", "integration": None, "friendly_name": "A"},
             "light.b": {"domain": "light", "integration": None, "friendly_name": "B"}}
    p1 = store.preview_diff(meta2, threshold=2)
    p2 = store.preview_diff(meta2, threshold=2)
    assert [e["entity_id"] for e in p1["new"]] == ["light.b"], p1
    assert [e["entity_id"] for e in p2["new"]] == ["light.b"], p2  # unchanged -> no baseline mutation
    print("ok test_preview_diff_is_readonly")


# ---- drift ledger ----------------------------------------------------------

def test_drift_dedup_and_resolve():
    _fresh_store()
    assert store.record_drift("new:lock.back", "new_entity", "lock.back", "d1") is True   # first time -> card
    assert store.record_drift("new:lock.back", "new_entity", "lock.back", "d1") is False  # standing -> no re-card
    store.set_card("new:lock.back", "card123")
    assert [d["key"] for d in store.list_open()] == ["new:lock.back"]
    # condition still present this run -> stays open
    assert store.resolve_absent({"new:lock.back"}) == []
    # condition cleared -> resolved, no longer open
    assert store.resolve_absent(set()) == ["new:lock.back"]
    assert store.list_open() == []
    print("ok test_drift_dedup_and_resolve")


# ---- last_verified staleness -----------------------------------------------

def test_stale_by_last_verified():
    _fresh_ks()
    now = int(time.time())
    old = now - 400 * 86400
    # a fact verified long ago via the explicit convention
    ks.commit(ks.propose("set", type="hvac", name="Furnace",
                         attrs={"model": "X", "last_verified": old})["token"])
    # a fact verified recently -> not stale
    ks.commit(ks.propose("set", type="hvac", name="Heat Pump",
                         attrs={"model": "Y", "last_verified": now})["token"])
    # a fact with no last_verified -> falls back to updated_at (just now) -> not stale at 180d
    ks.commit(ks.propose("set", type="breaker", name="Panel A", attrs={"slot": 4})["token"])

    stale = ks.stale_by_last_verified(age_days=180)
    ids = {s["id"]: s for s in stale}
    assert "hvac:furnace" in ids and ids["hvac:furnace"]["basis"] == "last_verified", stale
    assert "hvac:heat-pump" not in ids, stale
    assert "breaker:panel-a" not in ids, stale  # recent updated_at fallback
    print("ok test_stale_by_last_verified:", ids["hvac:furnace"])


if __name__ == "__main__":
    test_match_entity_glob_name()
    test_classify_index_episodic_idle_not_fault()
    test_find_successor()
    test_diff_new_removed_debounce()
    test_apply_diff_first_run_silent_then_detects()
    test_preview_diff_is_readonly()
    test_drift_dedup_and_resolve()
    test_stale_by_last_verified()
    print("\nall home_reconcile tests passed")
