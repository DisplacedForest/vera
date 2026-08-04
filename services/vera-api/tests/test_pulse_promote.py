import asyncio
import time

import pytest

from routers import pulse, pulse_store


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(pulse_store, "DB_PATH", str(tmp_path / "pulse.db"))
    yield


def _card(cid="c1", status="new", promoted_chat_id=None):
    pulse_store.insert_card({"id": cid, "created_at": int(time.time()), "day": "2026-08-04",
                             "status": status, "title": "t", "summary": "", "body": "",
                             "image_url": None, "tint": None, "sources": [], "inline_images": [],
                             "promoted_chat_id": promoted_chat_id})
    return cid


def test_promote_flips_status_without_a_chat_backend():
    cid = _card()
    out = asyncio.run(pulse.promote(cid))
    assert out == {"ok": True, "chat_id": None}
    assert pulse_store.get_card(cid)["status"] == "promoted"


def test_promote_is_idempotent_and_keeps_a_legacy_chat_id():
    cid = _card(status="promoted", promoted_chat_id="legacy")
    out = asyncio.run(pulse.promote(cid))
    assert out == {"ok": True, "chat_id": "legacy"}
    assert pulse_store.get_card(cid)["status"] == "promoted"


def test_bookmark_sets_the_status_without_a_chat_backend():
    cid = _card()
    out = asyncio.run(pulse.bookmark(cid, pulse.BookmarkBody(on=True)))
    assert out["ok"] is True and out["chat_id"] is None
    assert pulse_store.get_card(cid)["status"] == "bookmarked"


def test_unbookmark_returns_the_card_to_seen():
    cid = _card(status="bookmarked")
    asyncio.run(pulse.bookmark(cid, pulse.BookmarkBody(on=False)))
    assert pulse_store.get_card(cid)["status"] == "seen"


def test_missing_card_reports_not_found():
    assert asyncio.run(pulse.promote("nope")) == {"ok": False, "error": "not found"}
    assert asyncio.run(pulse.bookmark("nope", pulse.BookmarkBody(on=True))) == {
        "ok": False, "error": "not found"}
