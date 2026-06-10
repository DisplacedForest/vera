"""Shared grooming vocabulary tests — snapshot/op builders, the suppression store, and
the stale-snapshot guard. Deterministic; no LLM. Run: python3 -m pytest tests/test_groom_common.py
"""
import os
import sys
import tempfile

os.environ["GROOM_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "groom.db")
os.environ["KNOWLEDGE_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "k.db")
os.environ["VERA_MEMORY_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routers"))
import groom_common as gc  # noqa: E402
import knowledge_store as ks  # noqa: E402
import vera_memory_store as vm  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate():
    gc.DB_PATH = os.environ["GROOM_DB_PATH"]
    ks.DB_PATH = os.environ["KNOWLEDGE_DB_PATH"]
    yield


# --- snapshot builders -------------------------------------------------------------------------

def test_snap_belief_shape():
    s = gc.snap_belief({"id": "b1", "topic": "Net", "content": "UniFi", "tier": "core", "confidence": 0.7})
    assert s == {"kind": "belief", "id": "b1", "topic": "Net", "content": "UniFi",
                 "tier": "core", "confidence": 0.7}


def test_snap_entity_shape():
    s = gc.snap_entity({"id": "appliance:fridge", "type": "appliance", "name": "Fridge",
                        "attrs": {"brand": "LG"}})
    assert s["kind"] == "entity" and s["attrs"] == {"brand": "LG"} and s["name"] == "Fridge"


def test_snap_type_counts_migrated():
    s = gc.snap_type("appliance", {"required": ["brand"]},
                     [{"id": "appliance:a", "name": "A"}, {"id": "appliance:b", "name": "B"}])
    assert s["kind"] == "type" and s["entity_count"] == 2
    assert s["migrated"] == [{"id": "appliance:a", "name": "A"}, {"id": "appliance:b", "name": "B"}]


def test_op_carries_store_and_run_id():
    o = gc.op("gc", "knowledge", "orphan", run_id="r1", before=[{"kind": "entity", "id": "x"}])
    assert o["store"] == "knowledge" and o["run_id"] == "r1" and o["after"] is None


# --- suppression -------------------------------------------------------------------------------

def test_suppress_then_is_suppressed():
    assert not gc.is_suppressed("memory", "promote", "abc")
    gc.suppress("memory", "promote", "abc", reason="no")
    assert gc.is_suppressed("memory", "promote", "abc")
    # different op_type / identity stays unsuppressed
    assert not gc.is_suppressed("memory", "archive", "abc")
    assert not gc.is_suppressed("knowledge", "promote", "abc")


def test_suppress_idempotent():
    gc.suppress("knowledge", "codify", "service")
    gc.suppress("knowledge", "codify", "service")  # no raise, still one row
    assert gc.is_suppressed("knowledge", "codify", "service")


def test_belief_identity_stable_across_ids():
    a = gc.belief_identity("Net", "The home runs UniFi gear.")
    b = gc.belief_identity("Net", "The home runs UniFi gear.")
    assert a == b
    assert a != gc.belief_identity("Net", "Different content.")


# --- stale-snapshot guard ----------------------------------------------------------------------

def test_stale_false_for_deletion_reversal():
    # forget/gc carry after=None — reversal re-creates, never stale
    assert gc.stale_snapshot(gc.op("forget", "memory", "noise", before=[{"kind": "belief", "id": "b"}])) is False


def test_stale_memory_belief_unchanged_vs_mutated():
    eid = vm.write("Net", "UniFi everywhere", tier="archive")
    after = {"kind": "belief", "id": eid, "content": "UniFi everywhere", "tier": "archive"}
    o = gc.op("promote", "memory", "x", after=after)
    assert gc.stale_snapshot(o) is False
    vm.set_tier(eid, "core")  # a later run changed the tier
    assert gc.stale_snapshot(o) is True
    vm.delete(eid)            # or removed it entirely
    assert gc.stale_snapshot(o) is True


def test_stale_knowledge_entity_unchanged_vs_mutated():
    ks.commit(ks.propose("set", type="appliance", name="Fridge", attrs={"brand": "LG"})["token"])
    ent = ks.get("appliance:fridge")
    after = gc.snap_entity(ent)
    o = gc.op("merge", "knowledge", "x", after=after)
    assert gc.stale_snapshot(o) is False
    ks.commit(ks.propose("set", entity_id="appliance:fridge", type="appliance", name="Fridge",
                         attrs={"brand": "Bosch"})["token"])  # later edit
    assert gc.stale_snapshot(o) is True


def test_stale_type_never():
    o = gc.op("codify", "knowledge", "x", after=gc.snap_type("appliance", {"required": []}, []))
    assert gc.stale_snapshot(o) is False
