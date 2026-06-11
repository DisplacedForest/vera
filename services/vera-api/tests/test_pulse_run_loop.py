"""Pulse novelty-loop tests — the per-run delivery contract: re-triage (bounded rounds)
until at least PULSE_MIN_CARDS novel cards land, never past PULSE_MAX_CARDS, with every
proposal joining the exclusion list so retries can't re-pitch a rewording. The LLM and the
per-topic pipeline are mocked; the dedup gate inside research_topic is represented by the
mock returning None. Run: python3 -m pytest tests/test_pulse_run_loop.py
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from routers import pulse  # noqa: E402


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _harness(monkeypatch):
    """Neutralize everything around the loop: no sweep, no memories, no vision pause,
    empty feed corpus and profile unless a test overrides them."""
    monkeypatch.setattr(pulse.store, "sweep", lambda day: 0)
    monkeypatch.setattr(pulse.up, "get", lambda uid: {"name": "Z", "interests": [], "persona": None})

    async def _no_memories():
        return []

    async def _no_vision(pause):
        return None

    monkeypatch.setattr(pulse, "_get_memories", _no_memories)
    monkeypatch.setattr(pulse, "_vision", _no_vision)
    monkeypatch.setattr(pulse, "_recent_for_user", lambda uid: [])
    monkeypatch.setattr(pulse, "PULSE_MIN_CARDS", 2)
    monkeypatch.setattr(pulse, "PULSE_MAX_CARDS", 5)
    monkeypatch.setattr(pulse, "PULSE_TRIAGE_ROUNDS", 3)
    yield


def _topics(*titles):
    return [{"title": t, "angle": "", "query": t} for t in titles]


def _wire(monkeypatch, rounds, novel):
    """Triage returns rounds[i] on round i (then []); research_topic injects only titles
    in `novel` (others behave gated -> None). Returns the call journals."""
    triage_calls = []

    async def fake_triage(who, persona, interests, memories, exclusions, want, rnd):
        triage_calls.append({"want": want, "rnd": rnd, "exclusions": list(exclusions)})
        batch = rounds[rnd] if rnd < len(rounds) else []
        return batch[:want]

    researched = []

    async def fake_research(t, who, user_id, idx, provenance, errors):
        researched.append(t["title"])
        if t["title"] in novel:
            return {"title": t["title"]}
        return None  # the dedup gate (or empty synthesis)

    monkeypatch.setattr(pulse, "_triage", fake_triage)
    monkeypatch.setattr(pulse, "research_topic", fake_research)
    return triage_calls, researched


def test_floor_satisfied_by_second_round(monkeypatch):
    triage_calls, _ = _wire(
        monkeypatch,
        rounds=[_topics("A", "B", "C"), _topics("D", "E")],
        novel={"D", "E"},
    )
    out = run(pulse._do_run(pulse.PulseRequest()))
    assert out["injected"] == ["D", "E"]
    assert out["skipped"] == ["A", "B", "C"]
    assert [r["injected"] for r in out["rounds"]] == [[], ["D", "E"]]
    assert len(triage_calls) >= 2


def test_ceiling_stops_the_loop(monkeypatch):
    monkeypatch.setattr(pulse, "PULSE_MAX_CARDS", 3)
    triage_calls, _ = _wire(monkeypatch, rounds=[_topics("A", "B", "C")], novel={"A", "B", "C"})
    out = run(pulse._do_run(pulse.PulseRequest()))
    assert out["injected"] == ["A", "B", "C"]
    assert len(out["rounds"]) == 1
    assert triage_calls[0]["want"] == 3


def test_exclusions_accumulate_across_rounds(monkeypatch):
    monkeypatch.setattr(pulse, "_recent_for_user", lambda uid: [{"title": "Old card"}])
    triage_calls, _ = _wire(
        monkeypatch,
        rounds=[_topics("A", "B"), _topics("C", "D")],
        novel={"C", "D"},
    )
    run(pulse._do_run(pulse.PulseRequest()))
    assert triage_calls[0]["exclusions"] == ["Old card"]
    assert triage_calls[1]["exclusions"] == ["Old card", "A", "B"]


def test_rounds_exhausted_records_honest_underdelivery(monkeypatch):
    monkeypatch.setattr(pulse, "PULSE_TRIAGE_ROUNDS", 2)
    _wire(monkeypatch, rounds=[_topics("A"), _topics("B")], novel=set())
    out = run(pulse._do_run(pulse.PulseRequest()))
    assert out["injected"] == []
    assert len(out["rounds"]) == 2
    assert any("under floor" in e for e in out["errors"])


def test_empty_triage_round_ends_the_run(monkeypatch):
    _wire(monkeypatch, rounds=[[]], novel=set())
    out = run(pulse._do_run(pulse.PulseRequest()))
    assert out["rounds"] == []
    assert any("under floor" in e for e in out["errors"])


def test_explicit_max_cards_wins_but_clamps_to_ceiling(monkeypatch):
    triage_calls, _ = _wire(monkeypatch, rounds=[_topics("A")], novel={"A"})
    out = run(pulse._do_run(pulse.PulseRequest(max_cards=1)))
    assert out["injected"] == ["A"] and triage_calls[0]["want"] == 1
    # an explicit max_cards below PULSE_MIN_CARDS lowers the floor — no false under-delivery
    assert not any("under floor" in e for e in out["errors"])

    triage_calls2, _ = _wire(monkeypatch, rounds=[_topics("A", "B", "C", "D", "E", "F")], novel=set())
    run(pulse._do_run(pulse.PulseRequest(max_cards=99)))
    assert triage_calls2[0]["want"] == pulse.PULSE_MAX_CARDS


def _itopics(*pairs):
    return [{"title": t, "angle": "", "query": t, "interest": i} for t, i in pairs]


def test_interest_cap_limits_cards_per_interest_per_run(monkeypatch):
    _, researched = _wire(
        monkeypatch,
        rounds=[_itopics(("A", "Ashvale Rovers"), ("B", "Ashvale Rovers"), ("C", "winemaking"))],
        novel={"A", "B", "C"},
    )
    out = run(pulse._do_run(pulse.PulseRequest()))
    assert out["injected"] == ["A", "C"]  # B blocked: its interest already shipped this run
    assert "B" not in researched          # blocked before any research spend
    assert "B" in out["skipped"]
    assert any("interest cap" in e for e in out["errors"])


def test_gate_kills_are_counted_and_starved_run_warns(monkeypatch):
    markers = {
        "A": "skipped (already covered): A ≈ Old card",
        "B": "skipped (stale news): B — newest source undated",
        "C": "skipped (off-topic corpus): C — corpus about something else",
    }
    triage_calls = []

    async def fake_triage(who, persona, interests, memories, exclusions, want, rnd):
        triage_calls.append(rnd)
        return _topics("A", "B", "C") if rnd == 0 else []

    async def fake_research(t, who, user_id, idx, provenance, errors):
        errors.append(markers[t["title"]])
        return None

    monkeypatch.setattr(pulse, "_triage", fake_triage)
    monkeypatch.setattr(pulse, "research_topic", fake_research)
    out = run(pulse._do_run(pulse.PulseRequest()))
    assert out["gates"] == {"dedup": 1, "freshness": 1, "coherence": 1, "empty": 0, "interest_cap": 0}
    starved = [e for e in out["errors"] if e.startswith("starved run")]
    assert starved and "dedup=1" in starved[0] and "freshness=1" in starved[0]


def test_quiet_day_is_not_starved(monkeypatch):
    _wire(monkeypatch, rounds=[[]], novel=set())
    out = run(pulse._do_run(pulse.PulseRequest()))
    assert out["gates"] == {"dedup": 0, "freshness": 0, "coherence": 0, "empty": 0, "interest_cap": 0}
    assert not any(e.startswith("starved run") for e in out["errors"])


def test_cooled_interests_are_withheld_from_triage(monkeypatch):
    monkeypatch.setattr(pulse.up, "get", lambda uid: {
        "name": "Z", "persona": None,
        "interests": [{"topic": "Ashvale Rovers"}, {"topic": "winemaking"}]})
    monkeypatch.setattr(pulse.vi, "cooled", lambda topics: {"Ashvale Rovers"})
    captured = {}

    async def fake_triage(who, persona, interests, memories, exclusions, want, rnd):
        captured["interests"] = list(interests)
        return []

    monkeypatch.setattr(pulse, "_triage", fake_triage)
    run(pulse._do_run(pulse.PulseRequest()))
    assert captured["interests"] == ["winemaking"]


def test_shipped_card_stamps_its_interest(monkeypatch):
    stamped = []
    monkeypatch.setattr(pulse.vi, "cooled", lambda topics: set())
    monkeypatch.setattr(pulse.vi, "observe", lambda topic, **kw: stamped.append(("observe", topic)))
    monkeypatch.setattr(pulse.vi, "touch", lambda topic, **kw: stamped.append(("touch", topic)))
    _wire(monkeypatch, rounds=[_itopics(("A", "Ashvale Rovers"))], novel={"A"})
    run(pulse._do_run(pulse.PulseRequest()))
    assert ("touch", "Ashvale Rovers") in stamped


def test_triage_prompt_asks_for_serving_interest():
    assert '"interest"' in pulse.TRIAGE_SYS


def test_run_outcome_passes_inline_results_through():
    out = run(pulse.run_outcome({"ok": True, "expired": 3}))
    assert out == {"ok": True, "expired": 3}


def test_run_outcome_polls_to_terminal_record(monkeypatch):
    states = [
        {"state": "running"},
        {"state": "ok", "injected": ["A"], "gates": {"dedup": 5},
         "errors": ["starved run: 1/8 cards after 3 triage round(s); gate kills — dedup=5"]},
    ]
    monkeypatch.setattr(pulse.store, "get_run_status", lambda: states.pop(0))
    out = run(pulse.run_outcome({"ok": True, "run_id": "1", "state": "running"}, poll_secs=0))
    assert out["state"] == "ok" and out["injected"] == ["A"]
    assert out["gates"] == {"dedup": 5}
    assert any(w.startswith("starved run") for w in out["warnings"])


def test_runner_records_gates_in_the_run_record(monkeypatch):
    recorded = {}
    monkeypatch.setattr(pulse.store, "set_run_status", lambda d: recorded.update(d))

    async def fn(req):
        return {"injected": [], "topics": [], "errors": [], "rounds": [],
                "gates": {"dedup": 2, "freshness": 0, "coherence": 0, "empty": 0, "interest_cap": 0}}

    run(pulse._runner(fn, None, "rid", "run"))
    assert recorded["gates"]["dedup"] == 2


def test_triage_retry_prompt_and_temperature_escalation(monkeypatch):
    seen = []

    async def fake_vera(messages, temperature=0.4):
        seen.append({"temperature": temperature, "user": messages[1]["content"]})
        return '{"topics": []}'

    monkeypatch.setattr(pulse, "_vera", fake_vera)
    run(pulse._triage("Z", None, [], [], ["Old"], want=3, rnd=0))
    run(pulse._triage("Z", None, [], [], ["Old"], want=3, rnd=2))
    assert seen[0]["temperature"] == 0.4 and "already covered" not in seen[0]["user"]
    assert seen[1]["temperature"] == 0.9 and "Do NOT propose a rewording" in seen[1]["user"]
