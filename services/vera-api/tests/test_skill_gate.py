"""OWUI skill write-path governance — `author_skill` no longer writes directly: it stages a
gated `owui.skill_upsert` proposal, and only a confirmed token applies the write (audited in
the action log). The sanctioned `heartbeat` direct path is unaffected. Run under pytest."""
import asyncio

import pytest

from routers import authoring, actions, action_store
from routers import action_spec as sp


@pytest.fixture(autouse=True)
def _owui(monkeypatch):
    monkeypatch.setattr(authoring, "OWUI_BASE", "http://owui")
    monkeypatch.setattr(authoring, "OWUI_KEY", "k")


@pytest.fixture
def writes(monkeypatch):
    calls = []

    async def fake_upsert(sid, name, description, content):
        calls.append({"sid": sid, "name": name, "content": content})
        return sid

    monkeypatch.setattr(authoring, "_skill_upsert", fake_upsert)
    return calls


# --------------------------------------------------------------- the verb


def test_spec_verb_validates_and_is_gated():
    v = sp.SPEC["owui.skill_upsert"]["validate"]
    assert v({"name": "X", "content": "c"}) is None
    assert v({"name": "X", "content": "c", "id": "heartbeat"})   # heartbeat is sanctioned-direct, not gated
    assert v({"name": "", "content": "c"})                       # name required
    assert v({"name": "X", "content": ""})                       # content required
    assert sp.SPEC["owui.skill_upsert"]["risk"] == "high"
    assert sp.SPEC["owui.skill_upsert"]["autonomous"] is False
    assert "Hormuz" in sp.SPEC["owui.skill_upsert"]["preview"]({"name": "Hormuz", "content": "# x"})


def test_verb_never_enters_the_free_lane():
    assert not sp.is_autonomous("owui.skill_upsert")
    assert [v for v, s in sp.SPEC.items() if s["autonomous"]] == ["kitchen.mealie_import"]


# --------------------------------------------------------------- propose -> no write


def test_author_skill_stages_and_does_not_write(writes):
    res = asyncio.run(authoring.author_skill(
        authoring.SkillBody(name="Hormuz Baseline 2026", content="# Strait of Hormuz baseline")))
    assert res["proposed"] is True and res["token"]
    assert writes == []                                          # zero OWUI writes before confirm
    assert action_store.get(res["token"]) is not None           # a pending proposal exists


# --------------------------------------------------------------- confirm -> write + audit


def test_confirm_applies_and_audits(writes):
    res = asyncio.run(authoring.author_skill(
        authoring.SkillBody(name="Hormuz Baseline 2026", content="# baseline")))
    out = asyncio.run(actions._run_token(res["token"]))
    assert out["applied"] is True
    assert len(writes) == 1 and writes[0]["name"] == "Hormuz Baseline 2026"
    log = action_store.recent_log(20)
    assert any(r["verb"] == "owui.skill_upsert" and r["status"] == "applied" for r in log)


# --------------------------------------------------------------- heartbeat stays direct


def test_heartbeat_authoring_is_unaffected(writes):
    res = asyncio.run(authoring.author_heartbeat(authoring.HeartbeatBody(content="new standing instructions")))
    assert res["id"] == "heartbeat"
    assert len(writes) == 1 and writes[0]["sid"] == "heartbeat"   # direct write, no gate
