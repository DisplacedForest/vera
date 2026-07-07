"""Reminders proxy router tests. Standalone — run: pytest tests/test_reminders.py

Covers the contract: 503 with the standard disabled detail while the
apple_reminders integration is unconfigured, request pass-through shape when
configured, and 502 when the bridge is unreachable."""
import asyncio
import os
import sys

import aiohttp
import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from routers import integrations_store as ist  # noqa: E402
from routers import reminders as rm  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setattr(ist, "PATH", str(tmp_path / "integrations.json"))
    monkeypatch.delenv("VERA_REMINDERS_URL", raising=False)
    yield


def test_unconfigured_is_503():
    with pytest.raises(HTTPException) as e:
        asyncio.run(rm.lists())
    assert e.value.status_code == 503
    with pytest.raises(HTTPException) as e:
        asyncio.run(rm.create(rm.CreateBody(list="Shopping", title="milk")))
    assert e.value.status_code == 503


def test_proxies_when_configured(monkeypatch):
    monkeypatch.setenv("VERA_REMINDERS_URL", "http://bridge.example")
    seen = {}

    async def fake_call(method, path, params=None, body=None):
        seen.update(method=method, path=path, params=params, body=body)
        return {"ok": True}

    monkeypatch.setattr(rm, "_call", fake_call)
    assert asyncio.run(rm.reminders(list_name="Shopping", completed=False)) == {"ok": True}
    assert seen["params"] == {"completed": "false", "list": "Shopping"}
    asyncio.run(rm.update("abc", rm.UpdateBody(completed=True)))
    assert seen["method"] == "PATCH" and seen["path"] == "/reminders/abc"
    assert seen["body"] == {"completed": True}
    asyncio.run(rm.create(rm.CreateBody(list="Shopping", title="milk")))
    assert seen["method"] == "POST" and seen["body"] == {"list": "Shopping", "title": "milk"}


class _BoomSession:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def request(self, *a, **k):
        raise aiohttp.ClientError("boom")


def test_bridge_down_is_502(monkeypatch):
    monkeypatch.setenv("VERA_REMINDERS_URL", "http://bridge.example")
    monkeypatch.setattr(rm.aiohttp, "ClientSession", _BoomSession)
    with pytest.raises(HTTPException) as e:
        asyncio.run(rm.lists())
    assert e.value.status_code == 502
