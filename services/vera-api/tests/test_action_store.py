"""Action store unit tests. Standalone — run: python3 tests/test_action_store.py"""
import os
import sys
import tempfile

os.environ["ACTION_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "a.db")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routers"))
import action_store as a  # noqa: E402


def test_stage_and_get():
    t = a.stage("knowledge.set", {"type": "appliance", "name": "x"}, "Will set x", "low", True)
    p = a.get(t)
    assert p["verb"] == "knowledge.set"
    assert p["status"] == "pending"
    assert p["args"]["name"] == "x"
    assert p["risk"] == "low"
    assert p["reversible"] is True
    assert p["result"] is None


def test_token_dedupe():
    # same verb+args -> same content-hash token (preview/risk don't affect identity)
    t1 = a.stage("ha.service", {"domain": "climate", "service": "set_temperature"}, "p1", "medium", True)
    t2 = a.stage("ha.service", {"domain": "climate", "service": "set_temperature"}, "p2", "medium", True)
    assert t1 == t2
    # different args -> different token
    t3 = a.stage("ha.service", {"domain": "light", "service": "turn_on"}, "p3", "low", True)
    assert t3 != t1


def test_set_result_and_log():
    t = a.stage("health.check", {}, "check server", "none", True)
    a.set_result(t, {"ok": True, "detail": "fine"})
    p = a.get(t)
    assert p["status"] == "applied"
    assert p["result"]["ok"] is True
    assert any(r["token"] == t and r["status"] == "applied" for r in a.recent_log())


def test_dismiss():
    t = a.stage("ha.service", {"domain": "switch", "service": "turn_off"}, "p", "low", True)
    a.dismiss(t)
    assert a.get(t)["status"] == "dismissed"


def test_get_unknown():
    assert a.get("deadbeef") is None


if __name__ == "__main__":
    test_stage_and_get()
    test_token_dedupe()
    test_set_result_and_log()
    test_dismiss()
    test_get_unknown()
    print("OK")
