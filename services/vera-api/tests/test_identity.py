import asyncio

import pytest

from routers import household_store as hs
from routers import identity
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
    monkeypatch.setattr(hs, "DB_PATH", str(tmp_path / "household.db"))
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


def test_active_users_returns_the_seeded_owner(monkeypatch):
    monkeypatch.setenv("VERA_OWNER_ID", "zach")
    users = asyncio.run(identity.active_users())
    assert users == [{"id": "zach", "name": None}]


def test_an_existing_deployment_keeps_its_id_and_moves_no_rows(monkeypatch):
    monkeypatch.setenv("VERA_DEFAULT_USER", OWUI_UUID)
    pulse_store.init()
    pulse_store.insert_card({"id": "c1", "day": "2026-08-04", "title": "t",
                             "user_id": OWUI_UUID})
    pulse_store.mark_read(OWUI_UUID, "c1")
    up.set_persona(OWUI_UUID, name="Z", persona="direct")
    up.observe(OWUI_UUID, "winemaking", weight=2.0)

    hs.init()

    assert [m["id"] for m in hs.members()] == [OWUI_UUID]
    assert {c["user_id"] for c in pulse_store.list_cards()} == {OWUI_UUID}
    assert pulse_store.read_ids(OWUI_UUID) == {"c1"}
    assert up.get(OWUI_UUID)["persona"] == "direct"
    assert {i["topic"] for i in up.interests(OWUI_UUID)} == {"winemaking"}


def test_a_card_without_a_person_is_refused():
    pulse_store.init()
    with pytest.raises(ValueError):
        pulse_store.insert_card({"id": "c1", "day": "2026-08-04", "title": "t"})
    with pytest.raises(ValueError):
        pulse_store.insert_card({"id": "c2", "day": "2026-08-04", "title": "t", "user_id": "  "})


def test_two_members_do_not_cross_leak(monkeypatch):
    monkeypatch.setenv("VERA_OWNER_ID", "zach")
    hs.init()
    other = hs.add("Nephew")["id"]
    pulse_store.init()
    pulse_store.insert_card({"id": "mine", "day": "2026-08-04", "title": "mine", "user_id": "zach"})
    pulse_store.insert_card({"id": "theirs", "day": "2026-08-04", "title": "theirs", "user_id": other})
    pulse_store.mark_read("zach", "mine")

    assert {c["id"] for c in pulse_store.list_cards(user_id="zach")} == {"mine"}
    assert {c["id"] for c in pulse_store.list_cards(user_id=other)} == {"theirs"}
    assert pulse_store.read_ids("zach") == {"mine"}
    assert pulse_store.read_ids(other) == set()

    up.observe("zach", "winemaking")
    up.observe(other, "skateboarding")
    assert {i["topic"] for i in up.interests("zach")} == {"winemaking"}
    assert {i["topic"] for i in up.interests(other)} == {"skateboarding"}


def test_a_card_needs_an_audience(monkeypatch):
    monkeypatch.setenv("VERA_OWNER_ID", "zach")
    import asyncio as aio

    from routers import pulse
    pulse_store.init()
    with pytest.raises(ValueError):
        aio.run(pulse._inject("t", "b", user_id=None))
    with pytest.raises(ValueError):
        aio.run(pulse._inject("t", "b", user_id="  "))


def test_household_audience_is_explicit_and_lands_on_the_owner(monkeypatch):
    monkeypatch.setenv("VERA_OWNER_ID", "zach")
    import asyncio as aio

    from routers import pulse
    pulse_store.init()
    aio.run(pulse._inject("house", "b", user_id=pulse.HOUSEHOLD))
    assert {c["user_id"] for c in pulse_store.list_cards()} == {"zach"}


def test_a_card_for_a_person_keeps_that_person(monkeypatch):
    monkeypatch.setenv("VERA_OWNER_ID", "zach")
    import asyncio as aio

    from routers import pulse
    hs.init()
    other = hs.add("Nephew")["id"]
    pulse_store.init()
    aio.run(pulse._inject("theirs", "b", user_id=other))
    assert {c["user_id"] for c in pulse_store.list_cards()} == {other}


def test_a_read_mark_needs_a_person():
    pulse_store.init()
    for bad in (None, "", "   ", 7):
        with pytest.raises(ValueError):
            pulse_store.mark_read(bad, "c1")
    with pulse_store._conn() as c:
        assert c.execute("SELECT COUNT(*) FROM pulse_reads").fetchone()[0] == 0


def test_read_endpoint_marks_only_the_resolved_member(monkeypatch):
    monkeypatch.setenv("VERA_OWNER_ID", "zach")
    import asyncio as aio

    from routers import pulse
    hs.init()
    other = hs.add("Nephew")["id"]
    pulse_store.init()
    pulse_store.insert_card({"id": "c1", "day": "2026-08-04", "title": "t", "user_id": "zach"})
    aio.run(pulse.read(pulse.ReadBody(card_id="c1"), member=hs.get(other)))
    assert pulse_store.read_ids(other) == {"c1"}
    assert pulse_store.read_ids("zach") == set()


def test_the_read_body_carries_no_person():
    from routers import pulse
    assert "user_id" not in pulse.ReadBody.model_fields


def test_a_disabled_owner_is_refused_on_the_no_header_path(monkeypatch):
    from fastapi import HTTPException

    from routers import identity as ident
    monkeypatch.setenv("VERA_OWNER_ID", "zach")
    hs.init()
    hs.disable("zach")
    with pytest.raises(HTTPException) as e:
        ident.resolve_user()
    assert e.value.status_code == 403


def test_active_users_is_empty_when_every_member_is_disabled(monkeypatch):
    monkeypatch.setenv("VERA_OWNER_ID", "zach")
    hs.init()
    hs.disable("zach")
    assert asyncio.run(identity.active_users()) == []
