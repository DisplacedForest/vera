"""Knowledge store unit tests. Standalone — run: python3 tests/test_knowledge_store.py"""
import os
import sys
import tempfile

_KDB = os.path.join(tempfile.mkdtemp(), "k.db")
os.environ["KNOWLEDGE_DB_PATH"] = _KDB
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routers"))
import knowledge_store as ks  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_db():
    """knowledge_store is imported top-level by two test files that share
    one sys.modules entry + module-global DB_PATH. Pin this file's own DB before
    each test so cross-file state never bleeds in."""
    ks.DB_PATH = _KDB
    yield


def test_propose_commit_idempotent():
    p = ks.propose("set", type="appliance", name="Water Heater",
                   attrs={"brand": "Rheem", "installed": "2021"}, actor="vera")
    assert p["exists"] is False
    assert ("brand", None, "Rheem") in [(d["attr"], d["old"], d["new"]) for d in p["diff"]]
    assert "Rheem" in p["preview"]
    assert ks.commit(p["token"])["applied"] is True
    assert ks.commit(p["token"])["applied"] is False  # idempotent replay
    e = ks.get(p["entity_id"])
    assert e["attrs"]["brand"] == "Rheem"
    assert len(e["revisions"]) == 2


def test_update_diffs_against_existing():
    ks.commit(ks.propose("set", type="server", name="Homelab", attrs={"gpu": "model-a"})["token"])
    p = ks.propose("set", type="server", name="Homelab", attrs={"gpu": "model-b"})
    assert p["exists"] is True
    assert ("gpu", "model-a", "model-b") in [(d["attr"], d["old"], d["new"]) for d in p["diff"]]
    # unchanged attr produces no diff
    p2 = ks.propose("set", type="server", name="Homelab", attrs={"gpu": "model-a"})
    # gpu is still model-a in store (model-b was never committed), so no change
    assert p2["diff"] == []


def test_query():
    assert any(x["name"] == "Homelab" for x in ks.query(type="server"))
    assert any(x["name"] == "Water Heater" for x in ks.query(q="Rheem"))


def test_delete():
    ks.commit(ks.propose("set", type="misc", name="Temp", attrs={"x": "1"})["token"])
    assert ks.get("misc:temp") is not None
    ks.commit(ks.propose("delete", entity_id="misc:temp", type="misc", name="Temp")["token"])
    assert ks.get("misc:temp") is None


def test_promote_validates_and_records():
    ks.commit(ks.propose("set", type="vehicle", name="Truck",
                          attrs={"make": "Ford", "year": "2018"})["token"])
    schema = {"type": "object", "required": ["make", "year"],
              "properties": {"make": {"type": "string"}, "year": {"type": "string"}}}
    out = ks.promote("vehicle", schema, by="coding-agent")
    assert out["ok"] and out["migrated"] == 1 and out["invalid"] == []
    assert any(t["type"] == "vehicle" and t["promoted"] for t in ks.types())


def test_promote_refuses_invalid():
    ks.commit(ks.propose("set", type="boat", name="Dinghy", attrs={"make": "Zodiac"})["token"])
    schema = {"type": "object", "required": ["make", "year"]}  # year missing -> invalid
    out = ks.promote("boat", schema)
    assert out["ok"] is False and out["invalid"]
    assert not any(t["type"] == "boat" and t["promoted"] for t in ks.types())


def test_uncodify_reverses_promote():
    """A promoted type can be un-codified (the Restore/Reject path) — schema dropped,
    entities untouched, audited, idempotent."""
    ks.commit(ks.propose("set", type="drone", name="Mavic",
                          attrs={"make": "DJI", "year": "2022"})["token"])
    schema = {"type": "object", "required": ["make"], "properties": {"make": {"type": "string"}}}
    assert ks.promote("drone", schema)["ok"]
    assert any(t["type"] == "drone" and t["promoted"] for t in ks.types())
    out = ks.uncodify("drone")
    assert out["ok"] and out["removed"] is True
    assert not any(t["type"] == "drone" and t["promoted"] for t in ks.types())
    assert ks.get("drone:mavic") is not None  # the entity survives un-codification
    assert ks.uncodify("drone")["removed"] is False  # idempotent


if __name__ == "__main__":
    test_propose_commit_idempotent()
    test_update_diffs_against_existing()
    test_query()
    test_delete()
    test_promote_validates_and_records()
    test_promote_refuses_invalid()
    test_uncodify_reverses_promote()
    print("OK")
