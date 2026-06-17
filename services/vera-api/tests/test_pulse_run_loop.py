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

    async def _no_audit_phase(pending, errs, items_by_card=None):
        return None

    monkeypatch.setattr(pulse, "_get_memories", _no_memories)
    monkeypatch.setattr(pulse, "_vision", _no_vision)
    monkeypatch.setattr(pulse, "_audit_phase", _no_audit_phase)
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

    async def fake_research(t, who, user_id, idx, provenance, errors, defer_audit=False, outcome=None):
        researched.append(t["title"])
        if t["title"] in novel:
            return {"id": f"id-{t['title']}", "title": t["title"]}
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

    async def fake_research(t, who, user_id, idx, provenance, errors, defer_audit=False, outcome=None):
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
         "errors": ["starved run: 1/8 cards after 3 triage round(s); gate kills: dedup=5"]},
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


# --- end-of-run audit phase ------------------------------------------------------------
# The batched cross-model audit: one optional wake amortized across every injected card,
# revisions applied to the store, release only when this run's wake started the model.
# Bound at import because the loop harness above stubs pulse._audit_phase per-test.
_audit_phase = pulse._audit_phase


def _phase_harness(monkeypatch, wake="http://hooks/wake", release="http://hooks/stop",
                   wake_reply=None, wake_error=None, stamp="cross-model (m)"):
    """Wire _audit_phase's collaborators; returns the (hook calls, audits, store writes) journals."""
    journal = {"hooks": [], "audited": [], "applied": []}

    async def fake_hook(url):
        journal["hooks"].append(url)
        if url == wake and wake_error:
            raise wake_error
        return (wake_reply or {}) if url == wake else {}

    async def fake_audit(headline, body, sources, errs, title):
        journal["audited"].append(title)
        errs.append(f"claim audit: {title} — clean (coder)")
        return (f"{headline} (revised)", f"{body} (revised)", stamp,
                {"verdict": "clean", "unsupported": 0, "auditor": "coder"})

    monkeypatch.setattr(pulse, "AUDIT_WAKE_URL", wake)
    monkeypatch.setattr(pulse, "AUDIT_RELEASE_URL", release)
    monkeypatch.setattr(pulse, "_audit_hook", fake_hook)
    monkeypatch.setattr(pulse, "audit_claims", fake_audit)
    monkeypatch.setattr(pulse.store, "apply_audit",
                        lambda cid, title, body, audit: journal["applied"].append((cid, title, body, audit)))
    return journal


def _pending(*titles):
    return [({"id": f"id-{t}", "title": t, "body": f"{t} body"},
             [{"n": 1, "title": "S", "url": "http://s", "content": "x"}]) for t in titles]


def test_audit_phase_audits_every_card_and_applies_revisions(monkeypatch):
    journal = _phase_harness(monkeypatch)
    errs = []
    run(_audit_phase(_pending("A", "B"), errs))
    assert journal["audited"] == ["A", "B"]
    assert journal["applied"] == [
        ("id-A", "A (revised)", "A body (revised)", "cross-model (m)"),
        ("id-B", "B (revised)", "B body (revised)", "cross-model (m)"),
    ]


def test_audit_phase_brackets_with_wake_then_release(monkeypatch):
    journal = _phase_harness(monkeypatch)
    errs = []
    run(_audit_phase(_pending("A"), errs))
    assert journal["hooks"] == ["http://hooks/wake", "http://hooks/stop"]
    assert journal["audited"] == ["A"]  # audited between the two hook calls
    assert any(e == "audit wake: ok" for e in errs)


def test_audit_phase_wake_failure_falls_back_and_skips_release(monkeypatch):
    journal = _phase_harness(monkeypatch, wake_error=RuntimeError("connect refused"),
                             stamp="self (fallback)")
    errs = []
    run(_audit_phase(_pending("A"), errs))
    assert journal["hooks"] == ["http://hooks/wake"]  # no release: we never started it
    assert journal["audited"] == ["A"]  # the audit still runs (per-card fallback)
    assert journal["applied"][0][3] == "self (fallback)"
    assert any(e.startswith("audit wake failed") for e in errs)


def test_audit_phase_already_up_skips_release(monkeypatch):
    journal = _phase_harness(monkeypatch, wake_reply={"ok": True, "already_up": True})
    errs = []
    run(_audit_phase(_pending("A"), errs))
    assert journal["hooks"] == ["http://hooks/wake"]  # an already-up model is not ours to stop
    assert journal["audited"] == ["A"]
    assert any("already up" in e for e in errs)


def test_audit_phase_without_hooks_calls_none(monkeypatch):
    journal = _phase_harness(monkeypatch, wake="", release="")
    run(_audit_phase(_pending("A"), []))
    assert journal["hooks"] == []
    assert journal["audited"] == ["A"]  # today's behavior exactly, just batched


def test_audit_phase_wake_failure_real_machinery_stamps_fallback(monkeypatch):
    """The whole composition through the REAL audit_claims/_auditor: wake fails, the phase
    still audits, the unreachable audit endpoint drops each card to the main-model self-check,
    and the card is stamped self (fallback). (The fallback is reach-based by design — the wake
    is a warm-up, not a gate — so this is the outcome whenever the endpoint is actually down.)"""
    from routers import coder
    journal = {"hooks": [], "applied": []}

    async def failing_hook(url):
        journal["hooks"].append(url)
        raise RuntimeError("503 from the hook")

    async def coder_unreachable(messages, temperature, tools=None, max_tokens=None):
        raise RuntimeError("connect refused")

    async def vera_self_audit(messages, temperature=0.4):
        return '{"claims":[]}'

    monkeypatch.setattr(pulse, "AUDIT_WAKE_URL", "http://hooks/wake")
    monkeypatch.setattr(pulse, "AUDIT_RELEASE_URL", "http://hooks/stop")
    monkeypatch.setattr(pulse, "_audit_hook", failing_hook)
    monkeypatch.setattr(coder, "_endpoint", lambda: ("http://audit.example:1", "m"))
    monkeypatch.setattr(coder, "_llm", coder_unreachable)
    monkeypatch.setattr(pulse, "_vera", vera_self_audit)
    monkeypatch.setattr(pulse.store, "apply_audit",
                        lambda cid, title, body, audit: journal["applied"].append((cid, audit)))
    errs = []
    run(_audit_phase(_pending("A"), errs))
    assert journal["hooks"] == ["http://hooks/wake"]  # no release: nothing was started
    assert journal["applied"] == [("id-A", "self (fallback)")]
    assert any(e.startswith("audit wake failed") for e in errs)
    assert any("clean (main model (coder unreachable))" in e for e in errs)


def test_audit_hook_rejects_non_2xx():
    """A failed wake must read as failed: an HTTP error reply carries a JSON body too
    (FastAPI errors do), so the hook must raise on status, not just parse."""
    import aiohttp
    from aiohttp import web

    async def main():
        app = web.Application()
        app.router.add_post("/ok", lambda req: web.json_response({"ok": True, "already_up": False}))
        app.router.add_post("/busted", lambda req: web.json_response({"detail": "script not present"}, status=503))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        host, port = runner.addresses[0][:2]
        try:
            assert await pulse._audit_hook(f"http://{host}:{port}/ok") == {"ok": True, "already_up": False}
            with pytest.raises(aiohttp.ClientResponseError):
                await pulse._audit_hook(f"http://{host}:{port}/busted")
        finally:
            await runner.cleanup()

    run(main())


def test_audit_phase_with_nothing_injected_never_wakes(monkeypatch):
    journal = _phase_harness(monkeypatch)
    run(_audit_phase([], []))
    assert journal["hooks"] == [] and journal["audited"] == []


def test_audit_phase_card_failure_stays_per_card(monkeypatch):
    journal = _phase_harness(monkeypatch)

    async def flaky_audit(headline, body, sources, errs, title):
        if title == "A":
            raise RuntimeError("boom")
        journal["audited"].append(title)
        return headline, body, "cross-model (m)", {"verdict": "clean", "unsupported": 0, "auditor": "coder"}

    monkeypatch.setattr(pulse, "audit_claims", flaky_audit)
    errs = []
    run(_audit_phase(_pending("A", "B"), errs))
    assert journal["audited"] == ["B"]  # A's failure didn't take B down
    assert any("audit phase error" in e for e in errs)
    assert journal["hooks"] == ["http://hooks/wake", "http://hooks/stop"]  # release still fires


def test_run_loop_defers_audits_and_feeds_the_phase(monkeypatch):
    """The loop hands every injected card + its corpus to the phase, with defer_audit on."""
    captured = {}

    async def fake_phase(pending, errs, items_by_card=None):
        captured["pending"] = pending

    async def fake_triage(who, persona, interests, memories, exclusions, want, rnd):
        return _topics("A") if rnd == 0 else []

    async def fake_research(t, who, user_id, idx, provenance, errors, defer_audit=False, outcome=None):
        captured["defer_audit"] = defer_audit
        return {"id": f"id-{t['title']}", "title": t["title"], "_corpus": [{"n": 1, "content": "x"}]}

    monkeypatch.setattr(pulse, "_audit_phase", fake_phase)
    monkeypatch.setattr(pulse, "_triage", fake_triage)
    monkeypatch.setattr(pulse, "research_topic", fake_research)
    out = run(pulse._do_run(pulse.PulseRequest()))
    assert out["injected"] == ["A"]
    assert captured["defer_audit"] is True
    [(card, corpus)] = captured["pending"]
    assert card["title"] == "A" and corpus == [{"n": 1, "content": "x"}]
    assert "_corpus" not in card  # the ephemeral key never outlives the hand-off
