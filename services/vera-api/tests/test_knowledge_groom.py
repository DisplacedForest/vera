"""Knowledge-store grooming helper tests. Standalone — run: python3 tests/test_knowledge_groom.py

Covers the read-only analysis helpers + the audited merge composition that the nightly groom pass
(routers/knowledge_groom.py) is built on. The LLM-judged step in the router is exercised separately;
everything mechanical lives here so it is deterministic.
"""
import os
import sys
import tempfile
import time

_KDB = os.path.join(tempfile.mkdtemp(), "k.db")
os.environ["KNOWLEDGE_DB_PATH"] = _KDB
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routers"))
import knowledge_store as ks  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_db():
    """Pin this file's own knowledge DB before each test so it never
    shares state with test_knowledge_store (shared sys.modules + DB_PATH)."""
    ks.DB_PATH = _KDB
    yield


def _set(type, name, attrs, **kw):
    return ks.commit(ks.propose("set", type=type, name=name, attrs=attrs, **kw)["token"])


def test_dedup_clusters_groups_same_real_thing():
    # The motivating real case: the same water heater landed under two distinct slugs.
    _set("appliance", "Water Heater", {"brand": "Rheem", "installed": "2021"})
    _set("appliance", "Hot Water Heater", {"capacity_gal": "50", "model": "XE50"})
    # An unrelated appliance of the same type must NOT join the cluster.
    _set("appliance", "Dishwasher", {"brand": "Bosch"})
    clusters = ks.dedup_clusters()
    names = [sorted(e["name"] for e in c) for c in clusters]
    assert ["Hot Water Heater", "Water Heater"] in names
    assert all("Dishwasher" not in grp for grp in names)


def test_cluster_conflicts_flags_contradictions():
    a = {"attrs": {"installed": "2021", "brand": "Rheem"}}
    b = {"attrs": {"installed": "2019", "capacity_gal": "50"}}
    conflicts = ks.cluster_conflicts([a, b])
    assert "installed" in conflicts and set(conflicts["installed"]) == {"2021", "2019"}
    assert "brand" not in conflicts  # only present on one side -> not a conflict
    assert "capacity_gal" not in conflicts
    # disjoint, non-conflicting cluster is lossless
    assert ks.cluster_conflicts([{"attrs": {"x": 1}}, {"attrs": {"y": 2}}]) == {}


def test_apply_merge_is_lossless_and_audited():
    _set("network_device", "Router", {"vendor": "UniFi", "model": "UDM-Pro"})
    _set("network_device", "Gateway", {"ip": "10.0.0.1", "fw": "4.0.6"})
    canon, member = "network_device:router", "network_device:gateway"
    out = ks.apply_merge(canon, [member], by="coder")
    e = ks.get(canon)
    assert e is not None
    # canonical keeps its own attrs and absorbs the member's
    assert e["attrs"]["vendor"] == "UniFi" and e["attrs"]["ip"] == "10.0.0.1"
    assert ks.get(member) is None  # member superseded
    assert out["superseded"] == [member]
    # the merge is fully in the revision log: set(s) on canonical + a delete on the member
    ops = [r["op"] for r in e["revisions"]]
    assert "set" in ops
    deleted = ks.query()  # member gone from the graph
    assert all(x["id"] != member for x in deleted)


def test_apply_merge_dry_run_changes_nothing():
    _set("vehicle", "Truck", {"make": "Ford"})
    _set("vehicle", "F-150", {"year": "2018"})
    before = ks.get("vehicle:truck")["attrs"].copy()
    plan = ks.apply_merge("vehicle:truck", ["vehicle:f-150"], dry_run=True)
    assert plan["dry_run"] is True
    assert ks.get("vehicle:truck")["attrs"] == before  # untouched
    assert ks.get("vehicle:f-150") is not None  # member still present


def test_promotion_candidates_detects_stable_type():
    for n in ("Alpha", "Bravo", "Charlie"):
        _set("sensor", n, {"location": "garage", "metric": "temp"})
    cands = {c["type"]: c for c in ks.promotion_candidates(min_entities=3, min_coverage=0.8)}
    assert "sensor" in cands
    schema = cands["sensor"]["schema"]
    assert set(schema["required"]) == {"location", "metric"}
    assert cands["sensor"]["entities"] == 3
    # the derived schema must actually validate via the store's promote() path
    assert ks.promote("sensor", schema, by="coder")["ok"] is True


def test_promotion_candidates_excludes_promoted_and_small():
    # only one entity -> below min_entities
    _set("rare_type", "OnlyOne", {"a": "1"})
    cands = {c["type"] for c in ks.promotion_candidates(min_entities=3)}
    assert "rare_type" not in cands
    # 'sensor' was promoted in the previous test -> excluded now
    assert "sensor" not in cands


def test_orphan_empty_and_stale_flags():
    # an orphan: an entity left with no attributes
    _set("misc", "Empty", {})
    assert any(o["id"] == "misc:empty" for o in ks.orphan_entities())
    # staleness: backdate an entity's updated_at far into the past
    with ks._conn() as c:
        c.execute("UPDATE entity SET updated_at=? WHERE id=?",
                  (int(time.time()) - 400 * 86400, "vehicle:truck"))
    stale_ids = {s["id"] for s in ks.stale_entities(age_days=180)}
    assert "vehicle:truck" in stale_ids
    # empty_types: a promoted type whose entities are all gone
    ks.promote("ghost", {"type": "object", "required": []}, by="coder")
    assert "ghost" in ks.empty_types()


# --- Change-set snapshots the groom pass emits --------------------------------------------------
import groom_common as gcm  # noqa: E402


def test_promote_snapshot_carries_schema_and_migrated():
    """A knowledge promotion op must carry a reversible before (uncodified) + an after listing the
    entities the codified schema now governs — so the card shows WHICH type and HOW MANY, and the
    type can be un-codified on restore."""
    _set("widget", "Alpha", {"brand": "A", "model": "1"})
    _set("widget", "Beta", {"brand": "B", "model": "2"})
    _set("widget", "Gamma", {"brand": "C", "model": "3"})
    cand = next(c for c in ks.promotion_candidates(min_entities=3) if c["type"] == "widget")
    migrated = ks.query(type="widget", limit=100000)
    after = gcm.snap_type(cand["type"], cand["schema"], migrated)
    before = gcm.snap_type(cand["type"], None, [])
    assert after["entity_count"] == 3
    assert {m["name"] for m in after["migrated"]} == {"Alpha", "Beta", "Gamma"}
    assert after["schema"]["required"]  # the codified shape is recorded
    assert before["schema"] is None and before["entity_count"] == 0


def test_gc_snapshot_carries_recreatable_entity():
    """A GC op's before-snapshot must hold enough (type+name) to re-create the entity on restore."""
    _set("note", "Throwaway", {})  # empty attrs -> orphan
    orphan = next(o for o in ks.orphan_entities() if o["id"] == "note:throwaway")
    snap = gcm.snap_entity(orphan)
    assert snap["kind"] == "entity" and snap["type"] == "note" and snap["name"] == "Throwaway"
    # restore path: re-create from the snapshot, audited through the gated API
    ks.commit(ks.propose("delete", entity_id=orphan["id"], type=orphan["type"],
                         name=orphan["name"], source="groom", actor="coder")["token"])
    assert ks.get("note:throwaway") is None
    ks.commit(ks.propose("set", entity_id=snap["id"], type=snap["type"], name=snap["name"],
                         attrs=snap["attrs"], source="restore", actor="owner")["token"])
    assert ks.get("note:throwaway") is not None


if __name__ == "__main__":
    test_dedup_clusters_groups_same_real_thing()
    test_cluster_conflicts_flags_contradictions()
    test_apply_merge_is_lossless_and_audited()
    test_apply_merge_dry_run_changes_nothing()
    test_promotion_candidates_detects_stable_type()
    test_promotion_candidates_excludes_promoted_and_small()
    test_orphan_empty_and_stale_flags()
    print("OK")
