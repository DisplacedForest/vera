"""Structured per-item run outcomes (the pipeline drill-in's evidence). Each gate in
research_topic records {gate, reason, detail} into the `outcome` dict; the run loop assembles
out["items"] with injected / killed / cap statuses; the audit phase stamps per-card verdicts.
Run: python3 -m pytest tests/test_pulse_items.py
"""
import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from routers import pulse  # noqa: E402


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# --- research_topic gate capture -----------------------------------------------------

def test_dedup_gate_records_match_target(monkeypatch):
    async def covered(topic, user_id):
        return {"title": "Forest sign a striker"}
    monkeypatch.setattr(pulse, "already_covered", covered)
    oc = {}
    card = run(pulse.research_topic({"title": "Forest new signing", "query": "q"},
                                    who="Z", user_id="u", errors=[], outcome=oc))
    assert card is None
    assert oc == {"gate": "dedup", "reason": "already covered", "detail": "Forest sign a striker"}


def _pass_dedup_and_search(monkeypatch):
    async def none_covered(topic, user_id):
        return None

    async def empty_search(req):
        return SimpleNamespace(results=[])

    monkeypatch.setattr(pulse, "already_covered", none_covered)
    monkeypatch.setattr(pulse, "web_search", empty_search)


def test_freshness_gate_records_newest_date(monkeypatch):
    _pass_dedup_and_search(monkeypatch)

    async def stale(topic, newest):
        return True
    monkeypatch.setattr(pulse, "is_stale_news", stale)
    oc = {}
    card = run(pulse.research_topic({"title": "Old news", "query": "q"},
                                    who="Z", user_id="u", errors=[], outcome=oc))
    assert card is None
    assert oc == {"gate": "freshness", "reason": "stale news", "detail": "undated"}


def test_coherence_gate_records_corpus_subject(monkeypatch):
    _pass_dedup_and_search(monkeypatch)

    async def not_stale(topic, newest):
        return False

    async def off(topic, sources):
        return (True, "Bundesliga reserves")
    monkeypatch.setattr(pulse, "is_stale_news", not_stale)
    monkeypatch.setattr(pulse, "is_off_topic", off)
    oc = {}
    card = run(pulse.research_topic({"title": "Forest tactics", "query": "q"},
                                    who="Z", user_id="u", errors=[], outcome=oc))
    assert card is None
    assert oc == {"gate": "coherence", "reason": "off-topic corpus", "detail": "Bundesliga reserves"}


def test_empty_synthesis_records_gate(monkeypatch):
    _pass_dedup_and_search(monkeypatch)

    async def not_stale(topic, newest):
        return False

    async def on_topic(topic, sources):
        return (False, None)

    async def no_images(idx, q, tops):
        return []

    async def empty_vera(messages, temperature=0.4):
        return ""
    monkeypatch.setattr(pulse, "is_stale_news", not_stale)
    monkeypatch.setattr(pulse, "is_off_topic", on_topic)
    monkeypatch.setattr(pulse, "_gather_images", no_images)
    monkeypatch.setattr(pulse, "_vera", empty_vera)
    oc = {}
    card = run(pulse.research_topic({"title": "T", "query": "q"},
                                    who="Z", user_id="u", errors=[], outcome=oc))
    assert card is None
    assert oc == {"gate": "empty", "reason": "empty synthesis", "detail": None}


def _drive_success(monkeypatch, gen_ok=True, inline=None):
    _pass_dedup_and_search(monkeypatch)

    async def not_stale(topic, newest):
        return False

    async def on_topic(topic, sources):
        return (False, None)

    async def images(idx, q, tops):
        return inline or []

    async def vera(messages, temperature=0.4):
        sys_p = messages[0]["content"]
        if "Summarize" in sys_p:
            return "A short preview."
        if "cover art" in sys_p:
            return "a moody skyline"
        return "HEADLINE: Real headline\n\nThe body. [1]"

    async def gen(prompt, style, idx):
        if not gen_ok:
            raise RuntimeError("image service down")
        return "http://img/cover.png", "#112233"

    monkeypatch.setattr(pulse, "is_stale_news", not_stale)
    monkeypatch.setattr(pulse, "is_off_topic", on_topic)
    monkeypatch.setattr(pulse, "_gather_images", images)
    monkeypatch.setattr(pulse, "_vera", vera)
    monkeypatch.setattr(pulse, "_gen_image", gen)
    monkeypatch.setattr(pulse.store, "insert_card", lambda card: None)


def test_success_marks_cover_generated(monkeypatch):
    _drive_success(monkeypatch, gen_ok=True)
    oc = {}
    card = run(pulse.research_topic({"title": "T", "query": "q"}, who="Z", user_id="u",
                                    errors=[], defer_audit=True, outcome=oc))
    assert card is not None
    assert oc["cover_generated"] is True


def test_cover_fallback_marks_not_generated(monkeypatch):
    _drive_success(monkeypatch, gen_ok=False,
                   inline=[{"url": "http://img/real.jpg", "caption": "c", "srcN": 1}])
    oc = {}
    card = run(pulse.research_topic({"title": "T", "query": "q"}, who="Z", user_id="u",
                                    errors=[], defer_audit=True, outcome=oc))
    assert card is not None
    assert card["image_url"] == "http://img/real.jpg"   # promoted the gathered image
    assert oc["cover_generated"] is False


# --- run loop item assembly ----------------------------------------------------------

@pytest.fixture
def _loop(monkeypatch):
    monkeypatch.setattr(pulse.store, "sweep", lambda day: 0)
    monkeypatch.setattr(pulse.up, "get", lambda uid: {"name": "Z", "interests": [], "persona": None})
    monkeypatch.setattr(pulse.vi, "cooled", lambda topics: set())

    async def _no(*a, **k):
        return None
    monkeypatch.setattr(pulse, "_get_memories", _no)
    monkeypatch.setattr(pulse, "_vision", _no)

    async def _phase(pending, errs, items_by_card=None):
        # Stamp injected items the way the real phase would, so assembly is testable offline.
        for card, _ in pending:
            if items_by_card and card["id"] in items_by_card:
                items_by_card[card["id"]]["audit"] = {"verdict": "clean", "unsupported": 0, "auditor": "coder"}
    monkeypatch.setattr(pulse, "_audit_phase", _phase)
    monkeypatch.setattr(pulse, "_recent_for_user", lambda uid: [])
    monkeypatch.setattr(pulse, "PULSE_MIN_CARDS", 2)
    monkeypatch.setattr(pulse, "PULSE_MAX_CARDS", 5)
    monkeypatch.setattr(pulse, "PULSE_TRIAGE_ROUNDS", 1)


def test_do_run_items_carry_injected_and_killed_detail(monkeypatch, _loop):
    async def fake_triage(who, persona, interests, memories, exclusions, want, rnd):
        return [{"title": "Keep", "angle": "why", "query": "q", "interest": "forest"},
                {"title": "Drop", "angle": "", "query": "q"}] if rnd == 0 else []

    async def fake_research(t, who, user_id, idx, provenance, errors, defer_audit=False, outcome=None):
        if t["title"] == "Keep":
            if outcome is not None:
                outcome["cover_generated"] = True
            return {"id": "id-keep", "title": "Keep (real headline)", "_corpus": []}
        errors.append("skipped (off-topic corpus): Drop — corpus about cricket")
        if outcome is not None:
            outcome.update({"gate": "coherence", "reason": "off-topic corpus", "detail": "cricket"})
        return None

    monkeypatch.setattr(pulse, "_triage", fake_triage)
    monkeypatch.setattr(pulse, "research_topic", fake_research)
    out = run(pulse._do_run(pulse.PulseRequest()))

    items = {i["title"]: i for i in out["items"]}
    keep = items["Keep (real headline)"]
    assert keep["status"] == "injected" and keep["card_id"] == "id-keep"
    assert keep["cover_generated"] is True
    assert keep["interest"] == "forest"
    assert keep["audit"] == {"verdict": "clean", "unsupported": 0, "auditor": "coder"}

    drop = items["Drop"]
    assert drop["status"] == "killed" and drop["gate"] == "coherence"
    assert drop["detail"] == "cricket" and drop["round"] == 1


def test_do_run_items_record_interest_cap(monkeypatch, _loop):
    monkeypatch.setattr(pulse, "PULSE_MAX_PER_INTEREST", 1)

    async def fake_triage(who, persona, interests, memories, exclusions, want, rnd):
        return [{"title": "A", "angle": "", "query": "q", "interest": "forest"},
                {"title": "B", "angle": "", "query": "q", "interest": "forest"}] if rnd == 0 else []

    async def fake_research(t, who, user_id, idx, provenance, errors, defer_audit=False, outcome=None):
        return {"id": f"id-{t['title']}", "title": t["title"], "_corpus": []}

    monkeypatch.setattr(pulse, "_triage", fake_triage)
    monkeypatch.setattr(pulse, "research_topic", fake_research)
    out = run(pulse._do_run(pulse.PulseRequest()))

    cap = next(i for i in out["items"] if i["title"] == "B")
    assert cap["status"] == "cap" and cap["gate"] == "interest_cap" and cap["detail"] == "forest"


def test_do_run_always_emits_items_list(monkeypatch, _loop):
    async def empty_triage(*a, **k):
        return []
    monkeypatch.setattr(pulse, "_triage", empty_triage)
    out = run(pulse._do_run(pulse.PulseRequest()))
    assert out["items"] == []   # a quiet day still has the key, so the app degrades cleanly
