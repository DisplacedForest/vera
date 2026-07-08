import asyncio
import json

import pytest

from routers import health, pulse_store, updates, vein_engine

CTX = {"kind": "status", "options": {}, "providers": {}}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(pulse_store, "DB_PATH", str(tmp_path / "pulse.db"))
    monkeypatch.delenv("UNRAID_UPDATE_STATUS_PATH", raising=False)
    yield


def _run(coro):
    return asyncio.run(coro)


def test_status_blocks_registered_as_monitors():
    for name in ("service_health", "stack_updates"):
        assert name in vein_engine.BLOCKS
        assert name in vein_engine.MONITOR_BLOCKS


def test_service_health_emits_per_down_service(monkeypatch):
    async def fake_run():
        return [{"name": "open-webui", "ok": False, "detail": "HTTP 503"},
                {"name": "searxng", "ok": True, "detail": "HTTP 200"},
                {"name": "playwright", "ok": False, "detail": "tcp closed"}]
    monkeypatch.setattr(health, "_run", fake_run)
    items = _run(health._block_service_health([], {}, CTX))
    assert [i["key"] for i in items] == ["health:open-webui", "health:playwright"]
    assert all(i["category"] == "health" and i["severity"] == "alert" for i in items)


def test_service_health_all_green_emits_nothing(monkeypatch):
    async def fake_run():
        return [{"name": "open-webui", "ok": True, "detail": "HTTP 200"}]
    monkeypatch.setattr(health, "_run", fake_run)
    assert _run(health._block_service_health([], {}, CTX)) == []


def test_service_health_option_off_skips_probes(monkeypatch):
    async def fake_run():
        raise AssertionError("probes must not run")
    monkeypatch.setattr(health, "_run", fake_run)
    ctx = {"kind": "status", "options": {"src_service_health": False}, "providers": {}}
    assert _run(health._block_service_health([], {}, ctx)) == []


def _stub_components(monkeypatch, components):
    async def fake(sources):
        return updates._scope_components([dict(c) for c in components], sources)
    monkeypatch.setattr(updates, "_gather_components", fake)


def _stub_staging(monkeypatch):
    calls = {"n": 0}
    def fake_stage(verb, args, provenance, actor, a, b):
        calls["n"] += 1
        return {"token": "tok", "preview": "p", "risk": "low", "reversible": True}, None
    from routers import actions
    monkeypatch.setattr(actions, "_stage", fake_stage)
    async def fake_templated():
        return None
    monkeypatch.setattr(updates, "_templated_names", fake_templated)
    return calls


PENDING = [{"group": "Containers", "id": "docker:lscr.io/x/sonarr:latest",
            "image": "lscr.io/x/sonarr:latest", "name": "sonarr", "cur": None, "latest": None},
           {"group": "Network", "id": "update.udm_firmware", "name": "udm",
            "cur": "3.1", "latest": "3.2"}]


def test_stack_updates_emits_one_item_with_affordances(monkeypatch):
    _stub_components(monkeypatch, PENDING)
    calls = _stub_staging(monkeypatch)
    items = _run(updates._block_stack_updates([], {}, CTX))
    assert len(items) == 1
    it = items[0]
    assert it["key"] == "updates"
    assert it["title"] == "2 updates available"
    assert "**Containers**" in it["content"] and "**Network**" in it["content"]
    assert it["category"] == "update"
    assert len(it["items"]) == 2
    assert calls["n"] == 2


def test_stack_updates_quiet_when_current(monkeypatch):
    _stub_components(monkeypatch, [])
    assert _run(updates._block_stack_updates([], {}, CTX)) == []


def test_stack_updates_source_toggle_scopes(monkeypatch):
    _stub_components(monkeypatch, PENDING)
    _stub_staging(monkeypatch)
    ctx = {"kind": "status", "options": {"src_containers": False}, "providers": {}}
    items = _run(updates._block_stack_updates([], {}, ctx))
    assert items[0]["title"] == "1 update available"
    assert "sonarr" not in items[0]["content"]


def test_stack_updates_skips_staging_when_set_unchanged(monkeypatch):
    _stub_components(monkeypatch, PENDING)
    calls = _stub_staging(monkeypatch)
    first = _run(updates._block_stack_updates([], {}, CTX))[0]
    pulse_store.insert_card({
        "id": "c1", "day": "2026-01-01", "status": "seen", "title": first["title"],
        "summary": "", "body": first["content"], "kind": "status", "severity": "notice",
        "category": "update", "situation_key": "updates",
        "change_set": vein_engine._content_sig(first),
    })
    again = _run(updates._block_stack_updates([], {}, CTX))[0]
    assert "items" not in again
    assert calls["n"] == 2
