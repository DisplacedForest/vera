import asyncio
import os
import sqlite3
import time

import pytest

from routers import identity
from routers import identity_migrate as im
from routers import pulse_store
from routers import user_profile_store as up

OWUI_UUID = "3f2a9c1e-7b4d-4e0a-9a51-2c8e6f0d1b23"


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    from routers import integrations_store
    monkeypatch.setattr(integrations_store, "PATH", str(tmp_path / "integrations.json"), raising=False)
    for name in ("VERA_OWNER_ID", "VERA_OWNER_NAME", "VERA_DEFAULT_USER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(pulse_store, "DB_PATH", str(tmp_path / "pulse.db"))
    monkeypatch.setattr(up, "DB_PATH", str(tmp_path / "profiles.db"))
    yield


def test_owner_id_fallback_chain(monkeypatch):
    assert identity.owner_id() == "owner"
    monkeypatch.setenv("VERA_DEFAULT_USER", OWUI_UUID)
    assert identity.owner_id() == OWUI_UUID
    monkeypatch.setenv("VERA_OWNER_ID", "zach")
    assert identity.owner_id() == "zach"


def test_owner_name_and_record(monkeypatch):
    assert identity.owner_name() is None
    monkeypatch.setenv("VERA_OWNER_NAME", "Z")
    monkeypatch.setenv("VERA_OWNER_ID", "zach")
    assert identity.owner() == {"id": "zach", "name": "Z"}


def test_active_users_always_returns_the_owner(monkeypatch):
    monkeypatch.setenv("VERA_OWNER_ID", "zach")
    users = asyncio.run(identity.active_users())
    assert users == [{"id": "zach", "name": None}]


def _seed_stores():
    pulse_store.init()
    now = int(time.time())
    for cid, uid in (("c1", OWUI_UUID), ("c2", ""), ("c3", None)):
        pulse_store.insert_card({"id": cid, "created_at": now, "day": "2026-08-04",
                                 "status": "new", "title": cid, "summary": "", "body": "",
                                 "image_url": None, "tint": None, "sources": [],
                                 "inline_images": [], "promoted_chat_id": None,
                                 "user_id": uid, "kind": "research"})
    with pulse_store._conn() as c:
        c.execute("UPDATE cards SET user_id='' WHERE id='c2'")
        c.execute("UPDATE cards SET user_id=NULL WHERE id='c3'")
        c.execute("INSERT INTO pulse_reads(user_id, card_id, read_at) VALUES(?,?,?)",
                  (OWUI_UUID, "c1", now))
        c.execute("INSERT INTO pulse_reads(user_id, card_id, read_at) VALUES(?,?,?)",
                  ("", "c1", now))
    up.set_persona(OWUI_UUID, name="Z", persona="direct")
    up.set_persona("", persona="stale")
    up.observe(OWUI_UUID, "winemaking", weight=2.0, gloss="the craft")
    up.observe("", "winemaking", weight=1.0)
    up.observe("", "espresso", weight=0.5)


def _dump(path):
    c = sqlite3.connect(path)
    try:
        return list(c.iterdump())
    finally:
        c.close()


def test_migration_collapses_all_ids_to_the_owner(monkeypatch):
    monkeypatch.setenv("VERA_OWNER_ID", "zach")
    _seed_stores()
    out = im.run()
    assert out["owner_id"] == "zach"
    cards = pulse_store.list_cards()
    assert {c["user_id"] for c in cards} == {"zach"}
    assert pulse_store.read_ids("zach") == {"c1"}
    with pulse_store._conn() as c:
        assert c.execute("SELECT COUNT(*) FROM pulse_reads").fetchone()[0] == 1
    prof = up.get("zach")
    assert prof["name"] == "Z" and prof["persona"] in ("direct", "stale")
    topics = {i["topic"]: i for i in up.interests("zach")}
    assert set(topics) == {"winemaking", "espresso"}
    assert topics["winemaking"]["weight"] == 2.0
    assert topics["winemaking"]["gloss"] == "the craft"
    assert topics["winemaking"]["id"] == up._iid("zach", "winemaking")
    with up._conn() as c:
        assert c.execute("SELECT COUNT(*) FROM interest WHERE user_id != 'zach'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM profile").fetchone()[0] == 1


def test_migration_is_idempotent(monkeypatch):
    monkeypatch.setenv("VERA_OWNER_ID", "zach")
    _seed_stores()
    im.run()
    before = (_dump(pulse_store.DB_PATH), _dump(up.DB_PATH))
    out = im.run()
    assert out["pulse"] == {"skipped": True} and out["profiles"] == {"skipped": True}
    assert (_dump(pulse_store.DB_PATH), _dump(up.DB_PATH)) == before


def test_empty_string_default_user_rows_migrate_to_owner():
    _seed_stores()
    im.run()
    assert identity.owner_id() == "owner"
    assert {c["user_id"] for c in pulse_store.list_cards()} == {"owner"}
    assert os.path.exists(up.DB_PATH)
    assert {i["topic"] for i in up.interests("owner")} == {"winemaking", "espresso"}
