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
