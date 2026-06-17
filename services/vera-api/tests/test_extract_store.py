"""Conversation-extraction cursor store — per-source high-water marks so each source is
ingested incrementally and re-running is a no-op. Run under pytest."""
import os

import pytest

from routers import extract_store as es


@pytest.fixture(autouse=True)
def _fresh(tmp_path):
    es.DB_PATH = os.path.join(str(tmp_path), "extract.db")
    es.init()
    yield


def test_unseen_source_has_zero_cursor():
    assert es.get_cursor("owui") == {"last_ts": 0, "last_id": None}


def test_set_then_get_round_trips():
    es.set_cursor("owui", last_ts=1700, last_id="chat-9")
    assert es.get_cursor("owui") == {"last_ts": 1700, "last_id": "chat-9"}


def test_cursors_are_per_source():
    es.set_cursor("owui", last_ts=10, last_id="a")
    es.set_cursor("claude-code", last_ts=20, last_id="b")
    assert es.get_cursor("owui")["last_ts"] == 10
    assert es.get_cursor("claude-code")["last_ts"] == 20
