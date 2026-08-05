"""Shared grooming vocabulary tests — snapshot/op builders, the suppression store, and
the stale-snapshot guard. Deterministic; no LLM. Run: python3 -m pytest tests/test_groom_common.py
"""
import os
import sys
import tempfile

os.environ["GROOM_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "groom.db")
os.environ["KNOWLEDGE_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "k.db")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routers"))
import groom_common as gc  # noqa: E402
import knowledge_store as ks  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate():
    gc.DB_PATH = os.environ["GROOM_DB_PATH"]
    ks.DB_PATH = os.environ["KNOWLEDGE_DB_PATH"]
    yield


# --- snapshot builders -------------------------------------------------------------------------

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


# --- stale-snapshot guard ----------------------------------------------------------------------

def test_stale_false_for_deletion_reversal():
    # forget/gc carry after=None — reversal re-creates, never stale
    assert gc.stale_snapshot(gc.op("forget", "knowledge", "noise", before=[{"kind": "entity", "id": "e"}])) is False


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
