"""Free-lane endpoint discipline — POST /actions/auto accepts only verbs enrolled as
autonomous, validates, dedups by normalized URL, executes the shared executor, and audits
with auto=true. Run: python3 -m pytest tests/test_actions_auto.py"""
import asyncio

import pytest

from routers import action_spec as spec
from routers import action_store as astore
from routers import actions


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def harness(tmp_path, monkeypatch):
    monkeypatch.setattr(astore, "DB_PATH", str(tmp_path / "actions.db"))
    calls = []

    async def fake_import(args):
        calls.append(args)
        return {"ok": True, "slug": "pasta", "name": "Pasta",
                "url": "http://mealie.test/g/home/r/pasta"}

    monkeypatch.setitem(actions.EXECUTORS, "kitchen.mealie_import", fake_import)
    yield calls


def test_rejects_unenrolled_verbs(harness):
    for verb, args in (("ha.service", {"domain": "light", "service": "turn_on"}),
                       ("knowledge.set", {"type": "appliance", "name": "x"}),
                       ("kitchen.grocy_adjust", {"product_id": 1, "op": "add", "amount": 1}),
                       ("nonexistent.verb", {})):
        r = run(actions.auto(actions.Auto(verb=verb, args=args)))
        assert r["ok"] is False and r.get("rejected") is True
    assert harness == []                       # no executor ever ran
    assert astore.recent_log() == []           # nothing audited


def test_validates_args():
    r = run(actions.auto(actions.Auto(verb="kitchen.mealie_import", args={})))
    assert r["ok"] is False and "url" in r["error"]


def test_executes_and_audits(harness):
    r = run(actions.auto(actions.Auto(
        verb="kitchen.mealie_import", args={"url": "https://x.test/r/pasta"})))
    assert r["ok"] is True
    assert r["result"]["slug"] == "pasta"
    assert harness == [{"url": "https://x.test/r/pasta"}]
    row = astore.recent_log()[0]
    assert row["auto"] is True and row["status"] == "applied"
    assert row["verb"] == "kitchen.mealie_import"


def test_duplicate_url_skipped(harness):
    run(actions.auto(actions.Auto(
        verb="kitchen.mealie_import", args={"url": "https://x.test/r/pasta"})))
    # exact repeat, and trivially restyled variants, all dedup against the audit log
    for u in ("https://x.test/r/pasta", "HTTPS://X.TEST/r/pasta", "https://x.test/r/pasta/#step-2"):
        r = run(actions.auto(actions.Auto(verb="kitchen.mealie_import", args={"url": u})))
        assert r["ok"] is True and r["skipped"] == "duplicate"
    assert len(harness) == 1                   # executed exactly once
    assert len([r for r in astore.recent_log() if r["auto"]]) == 1


def test_failed_execution_audited_not_deduped(monkeypatch, harness):
    async def failing(args):
        harness.append(args)
        return {"ok": False, "error": "scrape failed"}

    monkeypatch.setitem(actions.EXECUTORS, "kitchen.mealie_import", failing)
    r = run(actions.auto(actions.Auto(
        verb="kitchen.mealie_import", args={"url": "https://x.test/r/flaky"})))
    assert r["ok"] is False
    assert astore.recent_log()[0]["status"] == "failed"
    # a failure is not an import — the same URL may be retried later
    r2 = run(actions.auto(actions.Auto(
        verb="kitchen.mealie_import", args={"url": "https://x.test/r/flaky"})))
    assert "skipped" not in r2
    assert len(harness) == 2


def test_flag_flip_regates(monkeypatch, harness):
    monkeypatch.setitem(spec.SPEC["kitchen.mealie_import"], "autonomous", False)
    r = run(actions.auto(actions.Auto(
        verb="kitchen.mealie_import", args={"url": "https://x.test/r/pasta"})))
    assert r["ok"] is False and r.get("rejected") is True
    assert harness == []
