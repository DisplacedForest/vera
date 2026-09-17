import asyncio

import pytest
from fastapi import HTTPException

from routers import household_store as hs
from routers import identity


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    from routers import integrations_store
    monkeypatch.setattr(integrations_store, "PATH", str(tmp_path / "integrations.json"), raising=False)
    for name in ("VERA_OWNER_ID", "VERA_OWNER_NAME", "VERA_DEFAULT_USER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(hs, "DB_PATH", str(tmp_path / "household.db"))
    monkeypatch.setenv("VERA_OWNER_ID", "owner")
    yield


def _resolve(vera_user=None, user_id=None):
    return identity.resolve_user(vera_user, user_id)


def test_no_header_resolves_to_the_owner():
    assert _resolve()["id"] == "owner"


def test_no_header_still_resolves_to_the_owner_with_a_full_roster():
    hs.init()
    hs.add("Nephew")
    assert _resolve()["id"] == "owner"


def test_header_selects_a_member():
    hs.init()
    m = hs.add("Nephew")
    assert _resolve(vera_user=m["id"])["id"] == m["id"]


def test_unknown_header_member_is_rejected():
    hs.init()
    with pytest.raises(HTTPException) as e:
        _resolve(vera_user="nobody")
    assert e.value.status_code == 403


def test_disabled_header_member_is_rejected():
    hs.init()
    m = hs.add("Nephew")
    hs.disable(m["id"])
    with pytest.raises(HTTPException) as e:
        _resolve(vera_user=m["id"])
    assert e.value.status_code == 403


def test_blank_header_falls_back_to_the_owner():
    hs.init()
    assert _resolve(vera_user="   ")["id"] == "owner"


def test_query_parameter_is_a_compatibility_path(caplog):
    hs.init()
    m = hs.add("Nephew")
    with caplog.at_level("INFO", logger="vera.identity"):
        assert _resolve(user_id=m["id"])["id"] == m["id"]
    assert any("user_id" in r.message for r in caplog.records)


def test_unknown_query_parameter_member_is_rejected():
    hs.init()
    with pytest.raises(HTTPException) as e:
        _resolve(user_id="nobody")
    assert e.value.status_code == 403


def test_header_wins_over_the_query_parameter():
    hs.init()
    a = hs.add("A")
    b = hs.add("B")
    assert _resolve(vera_user=a["id"], user_id=b["id"])["id"] == a["id"]


def test_the_dependency_delegates_to_the_resolver():
    hs.init()
    m = hs.add("Nephew")
    assert asyncio.run(identity.current_user(vera_user=m["id"]))["id"] == m["id"]


def test_active_users_returns_enabled_members():
    hs.init()
    m = hs.add("Nephew")
    users = asyncio.run(identity.active_users())
    assert [u["id"] for u in users] == ["owner", m["id"]]
    hs.disable(m["id"])
    assert [u["id"] for u in asyncio.run(identity.active_users())] == ["owner"]


def test_active_users_falls_back_to_the_owner_when_the_roster_is_empty(monkeypatch):
    monkeypatch.setattr(hs, "members", lambda include_disabled=False: [])
    assert asyncio.run(identity.active_users()) == [{"id": "owner", "name": None}]
