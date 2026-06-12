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


def test_log_auto_and_recent():
    # free-lane rows: auto-tagged, tokenless, visible in the main log
    a.log_auto("kitchen.mealie_import", {"url": "https://x.test/r/1"}, {"ok": True, "slug": "r1"})
    row = next(r for r in a.recent_log() if r["args"].get("url") == "https://x.test/r/1")
    assert row["auto"] is True
    assert row["token"] is None
    assert row["status"] == "applied"
    # gated rows stay auto=False
    t = a.stage("health.check", {"n": 2}, "check", "none", True)
    a.set_result(t, {"ok": True})
    assert next(r for r in a.recent_log() if r["token"] == t)["auto"] is False


def test_auto_recent_filters():
    import time
    a.log_auto("kitchen.mealie_import", {"url": "https://x.test/r/2"}, {"ok": True})
    a.log_auto("kitchen.mealie_import", {"url": "https://x.test/r/3"}, {"ok": False}, status="failed")
    a.log_auto("other.verb", {"url": "https://x.test/r/4"}, {"ok": True})
    rows = a.auto_recent("kitchen.mealie_import", time.time() - 60)
    urls = [r["args"]["url"] for r in rows]
    assert "https://x.test/r/2" in urls       # successful auto row counts
    assert "https://x.test/r/3" not in urls   # failed attempts don't
    assert "https://x.test/r/4" not in urls   # other verbs don't
    assert a.auto_recent("kitchen.mealie_import", time.time() + 60) == []  # window respected


if __name__ == "__main__":
    test_stage_and_get()
    test_token_dedupe()
    test_set_result_and_log()
    test_dismiss()
    test_get_unknown()
    test_log_auto_and_recent()
    test_auto_recent_filters()
    print("OK")
