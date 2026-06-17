"""The card apply path: a failed apply stays retryable (the content-hash token is not burned),
a successful update-card apply re-polls the updates check, and no LLM call lives in the path.
Run: python3 -m pytest tests/test_actions_apply.py
"""
import asyncio

import pytest

from routers import action_store as astore
from routers import actions
from routers import pulse
from routers import updates


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(astore, "DB_PATH", str(tmp_path / "actions.db"))


def _stage_docker(name="sonarr"):
    ok, err = actions._stage("docker.update", {"name": name, "image": f"img/{name}"},
                             "scheduled", "vera", None, None)
    assert err is None
    return ok["token"]


# --- defect 1: a failed apply does not burn the token ---------------------------------

def test_failed_apply_stays_retryable(monkeypatch):
    calls = []

    async def flaky(args):
        calls.append(args)
        if len(calls) == 1:
            return {"ok": False, "error": "unraid busy"}
        return {"ok": True, "container": "sonarr"}

    monkeypatch.setitem(actions.EXECUTORS, "docker.update", flaky)
    token = _stage_docker()

    r1 = run(actions._run_token(token))
    assert r1["applied"] is False
    assert astore.get(token)["status"] == "pending"   # not burned — retry is possible

    r2 = run(actions._run_token(token))
    assert r2["applied"] is True
    assert astore.get(token)["status"] == "applied"
    assert len(calls) == 2                              # the executor actually re-ran


def test_successful_apply_is_idempotent(monkeypatch):
    calls = []

    async def once(args):
        calls.append(args)
        return {"ok": True, "container": "sonarr"}

    monkeypatch.setitem(actions.EXECUTORS, "docker.update", once)
    token = _stage_docker()

    assert run(actions._run_token(token))["applied"] is True
    replay = run(actions._run_token(token))
    assert replay["applied"] is False                  # idempotent replay, no second run
    assert replay["result"]["ok"] is True
    assert len(calls) == 1


# --- defect 3: a successful update-card apply re-polls --------------------------------

def _update_card(token):
    return {"id": "card-up", "category": "update", "kind": "status",
            "items": [{"item_id": "row-1", "state": "pending",
                       "action": {"verb": "docker.update", "token": token}}]}


def test_update_card_apply_repolls(monkeypatch):
    async def ok_exec(args):
        return {"ok": True, "container": "sonarr"}

    monkeypatch.setitem(actions.EXECUTORS, "docker.update", ok_exec)
    card = _update_card(_stage_docker())
    monkeypatch.setattr(actions.pstore, "get_card", lambda cid: card)
    monkeypatch.setattr(actions.pstore, "set_items", lambda cid, items: None)

    rechecked = []

    async def fake_check(req):
        rechecked.append(req)
        return {"ok": True}

    monkeypatch.setattr(updates, "check", fake_check)

    async def scenario():
        res = await actions.card_item(actions.CardItemDecision(
            card_id="card-up", item_id="row-1", decision="approve"))
        await asyncio.sleep(0.05)   # let the fire-and-forget repoll task run
        return res

    res = run(scenario())
    assert res["ok"] is True and res["state"] == "approved"
    assert len(rechecked) == 1     # the apply triggered a fresh updates check


def test_failed_update_apply_does_not_repoll(monkeypatch):
    async def fail_exec(args):
        return {"ok": False, "error": "unraid busy"}

    monkeypatch.setitem(actions.EXECUTORS, "docker.update", fail_exec)
    card = _update_card(_stage_docker())
    monkeypatch.setattr(actions.pstore, "get_card", lambda cid: card)
    monkeypatch.setattr(actions.pstore, "set_items", lambda cid, items: None)

    rechecked = []

    async def fake_check(req):
        rechecked.append(req)
        return {"ok": True}

    monkeypatch.setattr(updates, "check", fake_check)

    async def scenario():
        res = await actions.card_item(actions.CardItemDecision(
            card_id="card-up", item_id="row-1", decision="approve"))
        await asyncio.sleep(0.05)
        return res

    res = run(scenario())
    assert res["ok"] is False           # apply failed
    assert rechecked == []              # nothing to re-poll on failure


# --- defect 4: the apply path never calls the model ----------------------------------

def test_apply_path_has_no_llm_leg(monkeypatch):
    async def _explode(*a, **k):
        raise AssertionError("the apply path must not call the model")

    monkeypatch.setattr(pulse, "_vera", _explode)

    async def ok_exec(args):
        return {"ok": True, "container": "sonarr"}

    monkeypatch.setitem(actions.EXECUTORS, "docker.update", ok_exec)
    item = {"item_id": "row-1", "state": "pending",
            "action": {"verb": "docker.update", "token": _stage_docker()}}

    res = run(actions._apply_item(item, "approve"))
    assert res["ok"] is True and item["state"] == "approved"
