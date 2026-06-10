"""Dreaming consolidation — mechanical phases. The LLM phases (REM opinions, journal) need
a live coder model and are exercised on a live run; everything deterministic lives here.

Run under pytest (the module uses package-relative imports)."""
import os

import pytest

from routers import dreaming, vera_memory_store as vm


@pytest.fixture(autouse=True)
def _fresh(tmp_path):
    """Each test gets a clean store. _conn() reads the module-global DB_PATH at call time, so
    reassigning these points the whole store at a fresh temp DB."""
    vm.DIR = str(tmp_path)
    vm.DB_PATH = os.path.join(vm.DIR, "store.db")
    vm.MEMORY_MD = os.path.join(vm.DIR, "MEMORY.md")
    dreaming.DREAMS_MD = os.path.join(vm.DIR, "DREAMS.md")
    vm.init()
    yield


def test_light_dedup_collapses_near_duplicates():
    # three near-identical scratch facts (the 10x-repeat pattern) + one distinct
    vm.write("UniFi RF", "UniFi 8.6 improves AI-driven RF optimization for IoT stability.",
             tier="scratch", kind="fact", confidence=0.6)
    vm.write("UniFi RF", "UniFi 8.6 improves AI-driven RF optimization for IoT device stability.",
             tier="scratch", kind="fact", confidence=0.7)
    vm.write("UniFi RF", "UniFi 8.6 improves the AI-driven RF optimization for IoT stability now.",
             tier="scratch", kind="fact", confidence=0.5)
    vm.write("Ashvale", "Ashvale Rovers finished 16th in 2025-26.", tier="scratch", kind="fact")
    out = dreaming.light_dedup()
    assert out["scanned"] == 4 and out["removed"] >= 2
    left = dreaming._scratch_facts()
    assert any("Ashvale" in (e["topic"] or "") for e in left)         # distinct survives
    assert sum(1 for e in left if e["topic"] == "UniFi RF") == 1      # cluster collapsed to one
    # the kept UniFi rep is the highest-confidence one (0.7)
    kept = [e for e in left if e["topic"] == "UniFi RF"][0]
    assert kept["confidence"] == 0.7


def test_deep_consolidate_promotes_durable_only():
    durable = vm.write("durable", "a well-grounded fact.", tier="scratch", kind="fact", confidence=0.9)
    thin = vm.write("thin", "a shaky low-confidence note.", tier="scratch", kind="fact", confidence=0.3)
    out = dreaming.deep_consolidate()
    assert out["promoted"] == 1
    assert [e for e in vm.recall("durable") if e["id"] == durable][0]["tier"] == "archive"
    assert [e for e in vm.recall("thin") if e["id"] == thin][0]["tier"] == "scratch"


def test_cluster_facts_groups_related_only():
    a = {"id": "1", "topic": "Ashvale", "content": "Ashvale Rovers finished 16th.", "confidence": 0.8}
    b = {"id": "2", "topic": "Ashvale", "content": "Ashvale Rovers had a turbulent season.", "confidence": 0.8}
    c = {"id": "3", "topic": "UniFi", "content": "UniFi shipped Wi-Fi 7 support.", "confidence": 0.8}
    clusters = dreaming._cluster_facts([a, b, c])
    assert len(clusters) == 1                       # only the two Ashvale facts cluster
    assert {e["id"] for e in clusters[0]} == {"1", "2"}


def test_reverify_candidates_excludes_private_facts():
    # a web-research belief (candidate) ...
    web = vm.write("UniFi 9.0", "UniFi 9.0 shipped Wi-Fi 7.", source="heartbeat", tier="core",
                   kind="fact", provenance={"query": "unifi 9.0 release", "sources": ["http://x"]})
    # ... a private/household fact (must NOT be web-reverified) ...
    vm.write("Household", "Alex and Jordan are married.", source="vera", tier="core", kind="fact")
    # ... and a heartbeat fact with no web provenance (not web-checkable) ...
    vm.write("Vibe", "the house feels calm.", source="heartbeat", tier="archive", kind="fact")
    ids = {f["id"] for f in dreaming._reverify_candidates()}
    assert web in ids and len(ids) == 1             # only the web-sourced research belief is a candidate
