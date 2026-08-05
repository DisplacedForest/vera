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
    store.snapshot("skill:notes", "old", note="v1")
    store.snapshot("skill:notes", "current", note="v2")
    s = store.skill_get("notes")
    assert s["content"] == "current"
    store.skill_upsert("notes", "Notes", "d", "edited")
    assert store.skill_get("notes")["content"] == "edited"


def test_seed_never_overwrites_live_row():
    store.skill_upsert("notes", "Notes", "d", "live")
    store.snapshot("skill:notes", "stale-history", note="old")
    assert store.skill_get("notes")["content"] == "live"


def test_skill_list_reports_size_not_content():
    store.skill_upsert("a", "A", "", "12345")
    rows = store.skill_list()
    assert rows[0]["size"] == 5 and "content" not in rows[0]


def test_skill_upsert_write_then_read_natively():
    store.snapshot("skill:notes", "standing rules", note="notes")
    sid = asyncio.run(authoring._skill_upsert("notes", "Notes", "d", "standing rules"))
    assert sid == "notes"
    assert store.skill_get("notes")["content"] == "standing rules"
    revs = store.revisions("skill:notes")
    assert revs and revs[0]["note"] == "notes"


def test_revert_restores_content_with_local_name():
    store.snapshot("skill:notes", "v1", note="v1")
    asyncio.run(authoring._skill_upsert("notes", "Notes", "d", "v1"))
    rev1 = store.revisions("skill:notes")[0]["id"]
    store.snapshot("skill:notes", "v2", note="v2")
    asyncio.run(authoring._skill_upsert("notes", "Notes", "d", "v2"))
    out = asyncio.run(authoring.revert(authoring.RevertBody(rev_id=rev1)))
    assert out["ok"] is True and out["reverted_to"] == rev1
    s = store.skill_get("notes")
    assert s["content"] == "v1" and s["name"] == "Notes"


def test_revert_rejects_non_skill_targets():
    rid = store.snapshot("notes:main", "x", note="n")
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        asyncio.run(authoring.revert(authoring.RevertBody(rev_id=rid)))
