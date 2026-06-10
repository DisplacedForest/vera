"""Vera memory store unit tests. Run: python3 tests/test_vera_memory_store.py"""
import os
import sys
import tempfile
import time

os.environ["VERA_MEMORY_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routers"))
import vera_memory_store as vm  # noqa: E402


def test_write_idempotent_and_recall():
    a = vm.write("US politics", "US political stability is fragile post-2024.", tier="core", confidence=0.9)
    b = vm.write("US politics", "US political stability is fragile post-2024.", tier="core")
    assert a == b  # content-hash id → idempotent
    assert any("fragile" in h["content"] for h in vm.recall("political"))


def test_core_digest_cap():
    for i in range(20):
        vm.write(f"belief {i}", "x" * 200, tier="core", confidence=0.5)
    assert len(vm.core_digest(char_cap=500)) <= 500


def test_recall_orders_core_first():
    vm.write("widget", "archive note about widgets", tier="archive")
    vm.write("widget", "CORE belief about widgets", tier="core", confidence=0.95)
    assert vm.recall("widget", limit=5)[0]["tier"] == "core"


def test_scratch_ttl_expires_and_not_in_core():
    vm.write("today", "scribble: defrost the carboy", tier="scratch", ttl_hours=-1)  # already expired
    vm.write("today2", "scribble: live note", tier="scratch", ttl_hours=24)
    live = vm.recall("scribble")
    assert any("live note" in h["content"] for h in live)
    assert not any("defrost" in h["content"] for h in live)   # expired scratch filtered from recall
    assert not any(e["topic"] == "today2" for e in vm.core())  # scratch never in core
    assert vm.purge_expired() >= 1                              # expired row purged


def test_groom_enforces_core_cap_and_mirrors():
    out = vm.groom(core_max=5)
    assert len(vm.core()) == 5 and out["demoted"] >= 1
    assert os.path.isfile(vm.MEMORY_MD)  # legible mirror written


def _raises_valueerror(fn):
    try:
        fn()
        return False
    except ValueError:
        return True


def test_default_kind_is_fact():
    eid = vm.write("dk topic", "a default-kind write.", tier="archive")
    got = [h for h in vm.recall("default-kind") if h["id"] == eid]
    assert got and got[0]["kind"] == "fact" and got[0]["fact_refs"] is None


def test_opinion_requires_grounding_facts():
    fact = vm.write("UniFi 9.0", "UniFi 9.0 shipped Wi-Fi 7 per the release notes.",
                    tier="archive", kind="fact")
    # empty fact_refs -> rejected
    assert _raises_valueerror(lambda: vm.write("take1", "I think Wi-Fi 7 matters.", kind="opinion"))
    # nonexistent ref -> rejected
    assert _raises_valueerror(
        lambda: vm.write("take2", "Anchored to nothing.", kind="opinion", fact_refs=["deadbeef0000"]))
    # a valid opinion (anchored to a real fact) is accepted and round-trips with its kind + refs
    op = vm.write("take_ok", "My read: Wi-Fi 7 is the headline.", kind="opinion", fact_refs=[fact])
    got = [h for h in vm.recall("headline") if h["id"] == op]
    assert got and got[0]["kind"] == "opinion" and got[0]["fact_refs"] == [fact]
    # citing an opinion (non-fact) -> rejected
    assert _raises_valueerror(
        lambda: vm.write("take3", "Citing a take, not a fact.", kind="opinion", fact_refs=[op]))


def test_recall_kind_filter():
    base = vm.write("kf base", "grounding fact for kf.", tier="archive", kind="fact")
    vm.write("kf opinion", "my read on kf.", kind="opinion", fact_refs=[base])
    facts = vm.recall("kf", kind="fact", limit=20)
    ops = vm.recall("kf", kind="opinion", limit=20)
    both = vm.recall("kf", limit=20)
    assert facts and all(h["kind"] == "fact" for h in facts)
    assert ops and all(h["kind"] == "opinion" for h in ops)
    assert any(h["kind"] == "opinion" for h in both) and any(h["kind"] == "fact" for h in both)


def test_core_digest_renders_opinion():
    fact = vm.write("cd fact", "Ashvale finished 16th in 2025-26 per the league table.",
                    tier="core", kind="fact", confidence=0.9)
    vm.write("cd take", "Ashvale's season was a C-minus.",
             tier="core", kind="opinion", fact_refs=[fact], confidence=0.9)
    dig = vm.core_digest()
    assert "my read: Ashvale's season was a C-minus." in dig
    assert "grounded in:" in dig  # the anchoring fact is shown


if __name__ == "__main__":
    test_write_idempotent_and_recall()
    test_core_digest_cap()
    test_recall_orders_core_first()
    test_scratch_ttl_expires_and_not_in_core()
    test_groom_enforces_core_cap_and_mirrors()
    test_default_kind_is_fact()
    test_opinion_requires_grounding_facts()
    test_recall_kind_filter()
    test_core_digest_renders_opinion()
    print("OK")
