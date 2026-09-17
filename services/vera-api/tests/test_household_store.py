import pytest

from routers import household_store as hs


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    from routers import integrations_store
    monkeypatch.setattr(integrations_store, "PATH", str(tmp_path / "integrations.json"), raising=False)
    for name in ("VERA_OWNER_ID", "VERA_OWNER_NAME", "VERA_DEFAULT_USER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(hs, "DB_PATH", str(tmp_path / "household.db"))
    yield


def test_seeds_the_configured_owner_on_first_init(monkeypatch):
    monkeypatch.setenv("VERA_OWNER_ID", "legacy-uuid")
    monkeypatch.setenv("VERA_OWNER_NAME", "Owner")
    hs.init()
    members = hs.members()
    assert [m["id"] for m in members] == ["legacy-uuid"]
    assert members[0]["name"] == "Owner"
    assert members[0]["enabled"] is True


def test_seeding_is_idempotent_and_never_reseeds(monkeypatch):
    monkeypatch.setenv("VERA_OWNER_ID", "legacy-uuid")
    hs.init()
    hs.disable("legacy-uuid")
    hs.init()
    assert [m["id"] for m in hs.members(include_disabled=True)] == ["legacy-uuid"]
    assert hs.members() == []


def test_seed_uses_the_owner_fallback_chain(monkeypatch):
    monkeypatch.setenv("VERA_DEFAULT_USER", "from-default")
    hs.init()
    assert [m["id"] for m in hs.members()] == ["from-default"]


def test_add_generates_an_opaque_id(monkeypatch):
    monkeypatch.setenv("VERA_OWNER_ID", "owner")
    hs.init()
    m = hs.add("Nephew")
    assert m["name"] == "Nephew"
    assert m["enabled"] is True
    assert m["id"] not in ("Nephew", "nephew", "owner")
    assert len(m["id"]) >= 16
    assert {x["id"] for x in hs.members()} == {"owner", m["id"]}


def test_add_does_not_collide_on_repeated_names(monkeypatch):
    monkeypatch.setenv("VERA_OWNER_ID", "owner")
    hs.init()
    a = hs.add("Sam")
    b = hs.add("Sam")
    assert a["id"] != b["id"]
    assert len(hs.members()) == 3


def test_rename_keeps_the_id(monkeypatch):
    monkeypatch.setenv("VERA_OWNER_ID", "owner")
    hs.init()
    m = hs.add("Nephew")
    hs.rename(m["id"], "Alex")
    assert hs.get(m["id"])["name"] == "Alex"
    assert hs.get(m["id"])["id"] == m["id"]


def test_disable_and_enable_round_trip(monkeypatch):
    monkeypatch.setenv("VERA_OWNER_ID", "owner")
    hs.init()
    m = hs.add("Nephew")
    hs.disable(m["id"])
    assert {x["id"] for x in hs.members()} == {"owner"}
    assert {x["id"] for x in hs.members(include_disabled=True)} == {"owner", m["id"]}
    hs.enable(m["id"])
    assert {x["id"] for x in hs.members()} == {"owner", m["id"]}


def test_get_returns_none_for_an_unknown_member(monkeypatch):
    monkeypatch.setenv("VERA_OWNER_ID", "owner")
    hs.init()
    assert hs.get("nobody") is None


def test_rename_and_disable_reject_unknown_members(monkeypatch):
    monkeypatch.setenv("VERA_OWNER_ID", "owner")
    hs.init()
    with pytest.raises(KeyError):
        hs.rename("nobody", "X")
    with pytest.raises(KeyError):
        hs.disable("nobody")


def test_add_requires_a_name(monkeypatch):
    monkeypatch.setenv("VERA_OWNER_ID", "owner")
    hs.init()
    with pytest.raises(ValueError):
        hs.add("   ")
