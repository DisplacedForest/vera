import asyncio

import pytest

from routers import authoring
from routers import authoring_store as store


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "authoring.db"))


def test_skill_round_trip_and_update():
    store.skill_upsert("field-notes", "Field Notes", "desc", "v1")
    s = store.skill_get("field-notes")
    assert s["name"] == "Field Notes" and s["description"] == "desc" and s["content"] == "v1"
    assert s["updated"] > 0
    store.skill_upsert("field-notes", "Field Notes", "desc", "v2")
    assert store.skill_get("field-notes")["content"] == "v2"


def test_skill_get_unknown_is_none():
    assert store.skill_get("nope") is None


def test_skill_seeds_from_latest_revision_once():
    store.snapshot("skill:heartbeat", "old", note="v1")
    store.snapshot("skill:heartbeat", "current", note="v2")
    s = store.skill_get("heartbeat")
    assert s["content"] == "current"
    store.skill_upsert("heartbeat", "Vera Heartbeat", "d", "edited")
    assert store.skill_get("heartbeat")["content"] == "edited"


def test_seed_never_overwrites_live_row():
    store.skill_upsert("heartbeat", "Vera Heartbeat", "d", "live")
    store.snapshot("skill:heartbeat", "stale-history", note="old")
    assert store.skill_get("heartbeat")["content"] == "live"


def test_skill_list_reports_size_not_content():
    store.skill_upsert("a", "A", "", "12345")
    rows = store.skill_list()
    assert rows[0]["size"] == 5 and "content" not in rows[0]


def test_heartbeat_write_then_read_natively():
    res = asyncio.run(authoring.author_heartbeat(authoring.HeartbeatBody(content="standing rules")))
    assert res["id"] == "heartbeat"
    assert store.skill_get("heartbeat")["content"] == "standing rules"
    revs = store.revisions("skill:heartbeat")
    assert revs and revs[0]["note"] == "heartbeat"


def test_revert_restores_content_with_local_name():
    asyncio.run(authoring.author_heartbeat(authoring.HeartbeatBody(content="v1")))
    rev1 = store.revisions("skill:heartbeat")[0]["id"]
    asyncio.run(authoring.author_heartbeat(authoring.HeartbeatBody(content="v2")))
    out = asyncio.run(authoring.revert(authoring.RevertBody(rev_id=rev1)))
    assert out["ok"] is True and out["reverted_to"] == rev1
    s = store.skill_get("heartbeat")
    assert s["content"] == "v1" and s["name"] == "Vera Heartbeat"


def test_revert_rejects_non_skill_targets():
    rid = store.snapshot("journal:main", "x", note="n")
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        asyncio.run(authoring.revert(authoring.RevertBody(rev_id=rid)))


def test_heartbeat_doc_prefers_store_and_falls_back(monkeypatch):
    from routers import heartbeat as hbmod
    assert asyncio.run(hbmod._heartbeat_doc()) == hbmod._file_checklist()
    store.skill_upsert("heartbeat", "Vera Heartbeat", "d", "from the store")
    assert asyncio.run(hbmod._heartbeat_doc()) == "from the store"
