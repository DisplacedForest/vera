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
    assert _run(pulse.is_stale_news({"title": "Yeast strain tannin research"}, "2026-06-01")) is False


def test_undated_corpus_passes_without_consulting_model(monkeypatch):
    calls = []

    async def vera_stale(messages, temperature=0.4):
        calls.append(1)
        return "STALE"

    monkeypatch.setattr(pulse, "_vera", vera_stale)
    assert _run(pulse.is_stale_news({"title": "anything"}, None)) is False
    assert calls == []  # no date evidence -> nothing to judge, model never consulted


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


def test_card_sys_requires_absolute_dates():
    card = pulse.CARD_SYS.format(img_instr="", who="Z", today="2026-06-10")
    assert "month and year" in card and "'in January 2025'" in card


# --- empty synthesis marker ----------------------------------------------------------

def test_empty_synthesis_is_marked(monkeypatch):
    from types import SimpleNamespace

    async def none_covered(topic, user_id):
        return None

    async def fake_search(req):
        return SimpleNamespace(results=[])

    async def not_stale(topic, newest):
        return False

    async def on_topic(topic, sources):
        return (False, None)

    async def no_images(idx, q, tops):
        return []

    async def empty_vera(messages, temperature=0.4):
        return ""

    monkeypatch.setattr(pulse, "already_covered", none_covered)
    monkeypatch.setattr(pulse, "web_search", fake_search)
    monkeypatch.setattr(pulse, "is_stale_news", not_stale)
    monkeypatch.setattr(pulse, "is_off_topic", on_topic)
    monkeypatch.setattr(pulse, "_gather_images", no_images)
    monkeypatch.setattr(pulse, "_vera", empty_vera)
    errs = []
    card = _run(pulse.research_topic({"title": "T", "query": "t"}, who="Z", user_id="u", errors=errs))
    assert card is None
    assert any(e.startswith("skipped (empty synthesis)") for e in errs)


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
        return '{"claims":[{"claim":"ok","source":1}]}', "coder", "cross-model (m)"
    monkeypatch.setattr(pulse, "_auditor", auditor)
    errs = []
    h, b, stamp = _run(pulse.audit_claims("Head", "Body text.", _srcs(), errs, "T"))
    assert (h, b) == ("Head", "Body text.")
    assert stamp == "cross-model (m)"
    assert errs == ["claim audit: T — clean (coder)"]


def test_audit_unsupported_revises(monkeypatch):
    calls = {"n": 0}
    async def auditor(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"claims":[{"claim":"Forest under Sean Dyche","source":"UNSUPPORTED"}]}', "coder", "cross-model (m)"
        return '{"claims":[]}', "coder", "cross-model (m)"  # re-audit of the revision, for the record
    async def vera(messages, temperature=0.4):
        assert "Forest under Sean Dyche" in messages[1]["content"]
        return "HEADLINE: Fixed Head\n\nRevised body without the claim."
    monkeypatch.setattr(pulse, "_auditor", auditor)
    monkeypatch.setattr(pulse, "_vera", vera)
    errs = []
    h, b, stamp = _run(pulse.audit_claims("Head", "Body under Sean Dyche.", _srcs(), errs, "T"))
    assert h == "Fixed Head" and b == "Revised body without the claim."
    assert stamp == "cross-model (m)"
    assert errs == ["claim audit: T — 1 unsupported, revised (coder)"]


def test_audit_machinery_failure_ships_original(monkeypatch):
    async def auditor(messages):
        raise RuntimeError("studio offline")
    monkeypatch.setattr(pulse, "_auditor", auditor)
    errs = []
    h, b, stamp = _run(pulse.audit_claims("Head", "Body.", _srcs(), errs, "T"))
    assert (h, b) == ("Head", "Body.")
    assert stamp == "none"  # no effective audit happened
    assert "audit unavailable" in errs[0]


def test_audit_unparseable_ships_original(monkeypatch):
    async def auditor(messages):
        return "I cannot really say", "coder", "cross-model (m)"
    monkeypatch.setattr(pulse, "_auditor", auditor)
    errs = []
    h, b, stamp = _run(pulse.audit_claims("Head", "Body.", _srcs(), errs, "T"))
    assert (h, b) == ("Head", "Body.")
    assert stamp == "none"  # a verdict nobody could parse is not an audit
    assert "unparseable" in errs[0]


def test_audit_empty_revision_ships_original(monkeypatch):
    async def auditor(messages):
        return '{"claims":[{"claim":"X","source":"UNSUPPORTED"}]}', "coder", "cross-model (m)"
    async def vera(messages, temperature=0.4):
        return ""
    monkeypatch.setattr(pulse, "_auditor", auditor)
    monkeypatch.setattr(pulse, "_vera", vera)
    errs = []
    h, b, stamp = _run(pulse.audit_claims("Head", "Body.", _srcs(), errs, "T"))
    assert (h, b) == ("Head", "Body.")
    assert stamp == "cross-model (m)"  # the audit DID run; only the revision failed
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
    raw, auditor, stamp = _run(pulse._auditor([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]))
    assert auditor == "main model (coder unreachable)" and raw == '{"claims":[]}'
    assert stamp == "self (fallback)"


def test_auditor_stamps_cross_model_with_the_model_name(monkeypatch):
    from routers import coder
    monkeypatch.setattr(coder, "_endpoint", lambda: ("http://coder.example:8084", "audit-model"))
    async def coder_up(messages, temperature, tools=None):
        return {"content": '{"claims":[]}'}
    monkeypatch.setattr(coder, "_llm", coder_up)
    _, auditor, stamp = _run(pulse._auditor([{"role": "user", "content": "u"}]))
    assert auditor == "coder" and stamp == "cross-model (audit-model)"


# --- cover image prompt inputs --------------------------------------------------------

def _research_with_synthesis(monkeypatch, synthesis, defer_audit=False):
    """Drive research_topic with a fixed synthesis; returns (image user msg, call order, card)."""
    from types import SimpleNamespace
    seen = {"img_usr": None, "order": []}

    async def none_covered(topic, user_id):
        return None

    async def fake_search(req):
        return SimpleNamespace(results=[])

    async def not_stale(topic, newest):
        return False

    async def on_topic(topic, sources):
        return (False, None)

    async def no_images(idx, q, tops):
        return []

    async def audit_passthrough(headline, body, sources, errs, title):
        seen["order"].append("audit")
        return headline, body, "cross-model (m)"

    async def vera(messages, temperature=0.4):
        sys_p = messages[0]["content"]
        if "Summarize this briefing" in sys_p:
            seen["order"].append("summary")
            return "Forest clinched a top-four finish on the final day."
        if "cover art" in sys_p:
            seen["order"].append("image")
            seen["img_usr"] = messages[1]["content"]
            return "A floodlit football stadium roaring on a spring evening"
        return synthesis

    async def fake_gen(prompt, style, idx):
        return "http://img/cover.png", "#223344"

    cards = []
    monkeypatch.setattr(pulse, "already_covered", none_covered)
    monkeypatch.setattr(pulse, "web_search", fake_search)
    monkeypatch.setattr(pulse, "is_stale_news", not_stale)
    monkeypatch.setattr(pulse, "is_off_topic", on_topic)
    monkeypatch.setattr(pulse, "_gather_images", no_images)
    monkeypatch.setattr(pulse, "audit_claims", audit_passthrough)
    monkeypatch.setattr(pulse, "_vera", vera)
    monkeypatch.setattr(pulse, "_gen_image", fake_gen)
    monkeypatch.setattr(pulse.store, "insert_card", cards.append)
    card = _run(pulse.research_topic({"title": "Nottingham Forest news", "query": "q"},
                                     who="Z", user_id="u", errors=[], defer_audit=defer_audit))
    return seen["img_usr"], seen["order"], card


def test_image_prompt_built_from_synthesis_not_triage_title(monkeypatch):
    img_usr, _, _ = _research_with_synthesis(
        monkeypatch, "HEADLINE: Forest Seal Top-Four Finish\n\nA decisive final-day win. [1]")
    assert img_usr.startswith("Headline: Forest Seal Top-Four Finish\n")
    assert "Summary: Forest clinched a top-four finish on the final day." in img_usr
    assert "Story: A decisive final-day win. [1]" in img_usr
    assert "Nottingham Forest news" not in img_usr  # triage working title never reaches the prompt


def test_image_prompt_falls_back_to_working_title_without_headline(monkeypatch):
    img_usr, _, _ = _research_with_synthesis(monkeypatch, "Body with no headline line. [1]")
    assert img_usr.startswith("Headline: Nottingham Forest news\n")


def test_summary_generated_before_cover_art(monkeypatch):
    _, order, card = _research_with_synthesis(
        monkeypatch, "HEADLINE: H\n\nBody. [1]")
    assert order == ["audit", "summary", "image"]
    assert card["summary"] == "Forest clinched a top-four finish on the final day."
    assert card["image_url"] == "http://img/cover.png" and card["tint"] == "#223344"


def test_inline_audit_stamps_the_card(monkeypatch):
    _, order, card = _research_with_synthesis(monkeypatch, "HEADLINE: H\n\nBody. [1]")
    assert "audit" in order
    assert card["audit"] == "cross-model (m)"
    assert "_corpus" not in card


def test_deferred_audit_skips_inline_and_hands_back_the_corpus(monkeypatch):
    _, order, card = _research_with_synthesis(monkeypatch, "HEADLINE: H\n\nBody. [1]",
                                              defer_audit=True)
    assert "audit" not in order  # the run loop's end-of-run phase owns it
    assert card["audit"] == "none"  # honest until the phase stamps the real mode
    assert "_corpus" in card  # full sources for the batched audit


def test_image_sys_carries_disambiguation_rule():
    assert "Disambiguate proper nouns" in pulse.IMAGE_SYS
    assert "No text, words, letters, logos, charts" in pulse.IMAGE_SYS
