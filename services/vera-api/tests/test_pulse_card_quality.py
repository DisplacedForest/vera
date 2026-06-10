"""Pulse card factual-integrity plumbing — the display headline comes from synthesis
(source-grounded) with the triage title as fallback, every pulse prompt knows today's date,
and source publish dates reach the numbered corpus. Run: python3 -m pytest tests/test_pulse_card_quality.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from routers import pulse  # noqa: E402
from routers.websearch import SearchResult  # noqa: E402


# --- headline extraction -------------------------------------------------------------

def test_headline_splits_off_body():
    h, b = pulse._split_headline("HEADLINE: Forest lock in Neco Williams to 2029\n\nI'm surfacing this because…")
    assert h == "Forest lock in Neco Williams to 2029"
    assert b == "I'm surfacing this because…"


def test_missing_headline_leaves_body_untouched():
    raw = "I'm surfacing this because the body starts immediately."
    h, b = pulse._split_headline(raw)
    assert h is None and b == raw


def test_empty_headline_counts_as_missing():
    raw = "HEADLINE:   \n\nBody text."
    h, b = pulse._split_headline(raw)
    assert h is None and b == raw


def test_headline_with_multiline_body():
    h, b = pulse._split_headline("HEADLINE: Title here\n\nPara one. [1]\n\nPara two. [2]\n")
    assert h == "Title here"
    assert b == "Para one. [1]\n\nPara two. [2]"


# --- date anchoring ------------------------------------------------------------------

def test_synthesis_and_thread_prompts_carry_today():
    today = time.strftime("%Y-%m-%d")
    card = pulse.CARD_SYS.format(img_instr="", who="Z", today=today)
    thread = pulse.THREAD_SYS.format(today=today)
    assert today in card and today in thread
    assert "HEADLINE" in card
    assert "never present a dated event as current" in card
    assert "only as they appear in the sources" in card


# --- source dates in the corpus ------------------------------------------------------

def test_corpus_includes_published_date_when_present():
    srcs = [
        {"n": 1, "title": "BBC", "url": "http://b", "content": "x", "published": "2026-06-08"},
        {"n": 2, "title": "Athletic", "url": "http://a", "content": "y"},
    ]
    corpus = pulse._numbered_corpus(srcs)
    assert "[1] BBC (published 2026-06-08)" in corpus
    assert "[2] Athletic\n" in corpus  # undated source stays undated


def test_search_result_carries_optional_published():
    assert SearchResult(title="t", url="u", content="c", rendered=False).published is None
    assert SearchResult(title="t", url="u", content="c", rendered=False,
                        published="2026-06-01").published == "2026-06-01"


# --- freshness gate ------------------------------------------------------------------

def _run(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


def test_newest_published_across_corpora():
    assert pulse._newest_published([]) is None
    assert pulse._newest_published([{"published": None}, {"title": "x"}]) is None
    assert pulse._newest_published([
        {"published": "2025-01-16"}, {"published": "2026-06-08"}, {"title": "undated"},
    ]) == "2026-06-08"


def test_stale_verdict_gates(monkeypatch):
    async def vera_stale(messages, temperature=0.4):
        return "STALE"
    monkeypatch.setattr(pulse, "_vera", vera_stale)
    assert _run(pulse.is_stale_news({"title": "Chris Wood Contract Extension"}, "2025-01-16")) is True


def test_fresh_verdict_passes(monkeypatch):
    async def vera_fresh(messages, temperature=0.4):
        return "FRESH"
    monkeypatch.setattr(pulse, "_vera", vera_fresh)
    assert _run(pulse.is_stale_news({"title": "Yeast strain tannin research"}, None)) is False


def test_gate_fails_open(monkeypatch):
    async def vera_boom(messages, temperature=0.4):
        raise RuntimeError("llm down")
    monkeypatch.setattr(pulse, "_vera", vera_boom)
    assert _run(pulse.is_stale_news({"title": "anything"}, "2025-01-16")) is False

    async def vera_garbage(messages, temperature=0.4):
        return "I think it might be old?"
    monkeypatch.setattr(pulse, "_vera", vera_garbage)
    assert _run(pulse.is_stale_news({"title": "anything"}, "2025-01-16")) is False


def test_gate_prompt_carries_today_and_date(monkeypatch):
    seen = {}
    async def vera_capture(messages, temperature=0.4):
        seen["sys"] = messages[0]["content"]; seen["usr"] = messages[1]["content"]
        return "FRESH"
    monkeypatch.setattr(pulse, "_vera", vera_capture)
    _run(pulse.is_stale_news({"title": "T", "angle": "A"}, "2025-01-16"))
    assert time.strftime("%Y-%m-%d") in seen["sys"]
    assert "Newest source date: 2025-01-16" in seen["usr"]
    _run(pulse.is_stale_news({"title": "T"}, None))
    assert "no dated sources" in seen["usr"]


def test_card_sys_requires_absolute_dates():
    card = pulse.CARD_SYS.format(img_instr="", who="Z", today="2026-06-10")
    assert "month and year" in card and "'in January 2025'" in card


# --- coherence gate ------------------------------------------------------------------

def test_off_topic_verdict_with_subject(monkeypatch):
    async def vera(messages, temperature=0.4):
        return "OFF-TOPIC Bundesliga reserve strikers"
    monkeypatch.setattr(pulse, "_vera", vera)
    off, found = _run(pulse.is_off_topic({"title": "Hjulmand's Midfield Control"}, []))
    assert off is True and found == "Bundesliga reserve strikers"


def test_on_topic_passes(monkeypatch):
    async def vera(messages, temperature=0.4):
        return "ON-TOPIC"
    monkeypatch.setattr(pulse, "_vera", vera)
    assert _run(pulse.is_off_topic({"title": "T"}, [])) == (False, None)


def test_coherence_gate_fails_open(monkeypatch):
    async def vera(messages, temperature=0.4):
        raise RuntimeError("down")
    monkeypatch.setattr(pulse, "_vera", vera)
    assert _run(pulse.is_off_topic({"title": "T"}, [])) == (False, None)


def test_coherence_prompt_carries_topic_and_corpus(monkeypatch):
    seen = {}
    async def vera(messages, temperature=0.4):
        seen["usr"] = messages[1]["content"]
        return "ON-TOPIC"
    monkeypatch.setattr(pulse, "_vera", vera)
    srcs = [{"n": 1, "title": "Some Article", "content": "snippet text here"}]
    _run(pulse.is_off_topic({"title": "My Topic", "angle": "why"}, srcs))
    assert "My Topic" in seen["usr"] and "[1] Some Article: snippet" in seen["usr"]


# --- claim audit ---------------------------------------------------------------------

def test_parse_audit_extracts_unsupported():
    raw = 'noise {"claims":[{"claim":"A is B","source":3},{"claim":"C manages D","source":"UNSUPPORTED"}]} tail'
    assert pulse._parse_audit(raw) == ["C manages D"]
    assert pulse._parse_audit('{"claims":[{"claim":"A","source":1}]}') == []
    assert pulse._parse_audit("not json at all") is None
    assert pulse._parse_audit('{"claims": "wrong shape"}') is None


def _srcs():
    return [{"n": 1, "title": "S", "url": "http://s", "content": "x"}]


def test_audit_clean_body_unchanged(monkeypatch):
    async def auditor(messages):
        return '{"claims":[{"claim":"ok","source":1}]}', "coder"
    monkeypatch.setattr(pulse, "_auditor", auditor)
    errs = []
    h, b = _run(pulse.audit_claims("Head", "Body text.", _srcs(), errs, "T"))
    assert (h, b) == ("Head", "Body text.")
    assert errs == ["claim audit: T — clean (coder)"]


def test_audit_unsupported_revises(monkeypatch):
    calls = {"n": 0}
    async def auditor(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"claims":[{"claim":"Forest under Sean Dyche","source":"UNSUPPORTED"}]}', "coder"
        return '{"claims":[]}', "coder"  # re-audit of the revision, for the record
    async def vera(messages, temperature=0.4):
        assert "Forest under Sean Dyche" in messages[1]["content"]
        return "HEADLINE: Fixed Head\n\nRevised body without the claim."
    monkeypatch.setattr(pulse, "_auditor", auditor)
    monkeypatch.setattr(pulse, "_vera", vera)
    errs = []
    h, b = _run(pulse.audit_claims("Head", "Body under Sean Dyche.", _srcs(), errs, "T"))
    assert h == "Fixed Head" and b == "Revised body without the claim."
    assert errs == ["claim audit: T — 1 unsupported, revised (coder)"]


def test_audit_machinery_failure_ships_original(monkeypatch):
    async def auditor(messages):
        raise RuntimeError("studio offline")
    monkeypatch.setattr(pulse, "_auditor", auditor)
    errs = []
    h, b = _run(pulse.audit_claims("Head", "Body.", _srcs(), errs, "T"))
    assert (h, b) == ("Head", "Body.")
    assert "audit unavailable" in errs[0]


def test_audit_unparseable_ships_original(monkeypatch):
    async def auditor(messages):
        return "I cannot really say", "coder"
    monkeypatch.setattr(pulse, "_auditor", auditor)
    errs = []
    h, b = _run(pulse.audit_claims("Head", "Body.", _srcs(), errs, "T"))
    assert (h, b) == ("Head", "Body.")
    assert "unparseable" in errs[0]


def test_audit_empty_revision_ships_original(monkeypatch):
    async def auditor(messages):
        return '{"claims":[{"claim":"X","source":"UNSUPPORTED"}]}', "coder"
    async def vera(messages, temperature=0.4):
        return ""
    monkeypatch.setattr(pulse, "_auditor", auditor)
    monkeypatch.setattr(pulse, "_vera", vera)
    errs = []
    h, b = _run(pulse.audit_claims("Head", "Body.", _srcs(), errs, "T"))
    assert (h, b) == ("Head", "Body.")
    assert "revision empty" in errs[0]


def test_card_sys_carries_current_state_rule():
    card = pulse.CARD_SYS.format(img_instr="", who="Z", today="2026-06-10")
    assert "Current-state attributions" in card and "leave the holder unnamed" in card


def test_auditor_falls_back_when_coder_unreachable(monkeypatch):
    from routers import coder
    monkeypatch.setattr(coder, "_endpoint", lambda: ("http://coder.example:8084", "m"))
    async def coder_down(messages, temperature, tools=None):
        raise RuntimeError("connect call failed")
    monkeypatch.setattr(coder, "_llm", coder_down)
    async def vera(messages, temperature=0.4):
        return '{"claims":[]}'
    monkeypatch.setattr(pulse, "_vera", vera)
    raw, auditor = _run(pulse._auditor([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]))
    assert auditor == "main model (coder unreachable)" and raw == '{"claims":[]}'
