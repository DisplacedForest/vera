"""Journal integrity residue after the Pulse v2 journal-as-view rebuild: a watch's material
facts cannot carry future-dated (fabricated) history, the legacy document fallback never
over-claims "you asked", and structured origin is read from the stamped marker fact.
Run: python3 -m pytest tests/test_journal_integrity.py
"""
import asyncio
import logging

from routers import editor
from routers import journal
from routers import profile_graph_store as pg


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# --- the pure future-date helper -----------------------------------------------------

def test_future_dated_only_flags_after_today():
    today = "2026-06-12"
    assert editor.future_dated("2026-06-13: strikes launched", today) is True
    assert editor.future_dated("2026-06-12: as of today", today) is False   # today is current
    assert editor.future_dated("2026-05-01: last month", today) is False
    assert editor.future_dated("no date here at all", today) is False
    assert editor.future_dated("watching the lumber market", today) is False


# --- author_watch drops future-dated material ----------------------------------------

def _capture_author(monkeypatch):
    captured = {}

    async def fake_embed(text):
        return None

    def fake_merge(*, type, label, embedding, facts=None, now=None, **k):
        captured["facts"] = facts
        return "node-1"

    monkeypatch.setattr(pg, "embed", fake_embed)
    monkeypatch.setattr(pg, "merge_or_create", fake_merge)
    return captured


def test_author_watch_drops_future_dated_material(monkeypatch, caplog):
    captured = _capture_author(monkeypatch)
    now = int(editor.datetime(2026, 6, 12, 12, tzinfo=editor.timezone.utc).timestamp())

    with caplog.at_level(logging.WARNING):
        run(editor.author_watch(
            "Strait of Hormuz",
            facts=["2026-06-11: tankers rerouted", "2026-06-13: strikes launched"],
            origin="watch", now=now))

    texts = [f["text"] for f in captured["facts"]]
    assert "2026-06-11: tankers rerouted" in texts        # past observation kept
    assert "2026-06-13: strikes launched" not in texts    # future-dated history dropped
    assert any(f["source"] == "journal:origin" for f in captured["facts"])
    assert any("future-dated" in r.message for r in caplog.records)


def test_author_watch_keeps_present_and_undated(monkeypatch):
    captured = _capture_author(monkeypatch)
    now = int(editor.datetime(2026, 6, 12, 12, tzinfo=editor.timezone.utc).timestamp())
    run(editor.author_watch("Lumber market",
                            facts=["watching cedar futures", "2026-06-12: price ticked up"],
                            origin="self", now=now))
    texts = [f["text"] for f in captured["facts"]]
    assert "watching cedar futures" in texts and "2026-06-12: price ticked up" in texts


# --- legacy fallback never over-claims -----------------------------------------------

def test_legacy_fallback_origin_is_always_self():
    doc = ("## Strait of Hormuz\n"
           "Origin: user-provided watch list carried over from signals\n"
           "- watching the strait\n")
    entries = journal.parse_document(doc)
    assert entries[0]["origin"] == "self"   # the "user" phrasing must not read as requested


# --- structured origin is read from the marker fact (pins SER-192) -------------------

def test_origin_reads_the_stamped_marker():
    requested = {"facts": [{"text": "requested", "source": "journal:origin", "observed_at": 1},
                           {"text": "watching", "source": "journal:material", "observed_at": 1}]}
    assert editor._origin(requested) == "requested"
    self_node = {"facts": [{"text": "self", "source": "journal:origin", "observed_at": 1}]}
    assert editor._origin(self_node) == "self"
    no_marker = {"facts": [{"text": "watching", "source": "journal:material", "observed_at": 1}]}
    assert editor._origin(no_marker) == "self"   # absent marker under-claims
