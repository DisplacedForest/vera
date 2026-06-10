"""Env naming migration tests — canonical names win, deprecated aliases still work for
one release, and a production .env written entirely in old names keeps resolving through
the integration registry. Run: python3 -m pytest tests/test_env_compat.py
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from routers import env_compat as ec  # noqa: E402
from routers import integrations as ig  # noqa: E402
from routers import integrations_store as ist  # noqa: E402

_ALL = [n for pair in ec.ALIASES.items() for n in pair]


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setattr(ist, "PATH", str(tmp_path / "integrations.json"))
    for n in _ALL:
        monkeypatch.delenv(n, raising=False)
    yield


def test_canonical_name_wins(monkeypatch):
    monkeypatch.setenv("SEARXNG_BASE", "http://new")
    monkeypatch.setenv("SEARXNG_URL", "http://old")
    assert ec.read("SEARXNG_BASE") == "http://new"


def test_deprecated_name_falls_back(monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", "http://old")
    assert ec.read("SEARXNG_BASE") == "http://old"
    assert ec.read("SEARXNG_BASE", "dflt") == "http://old"
    assert ec.read("GROCY_BASE", "dflt") == "dflt"


def test_deprecated_in_use_flags_only_unmigrated(monkeypatch):
    monkeypatch.setenv("GROCY_API_KEY", "k")                 # old only -> flagged
    monkeypatch.setenv("MEALIE_BASE", "http://m")            # new only -> not flagged
    monkeypatch.setenv("OVERSEERR_BASE", "http://o")         # both set -> migrated, not flagged
    monkeypatch.setenv("OVERSEERR_URL", "http://o-old")
    flagged = dict(ec.deprecated_in_use())
    assert flagged.get("GROCY_API_KEY") == "GROCY_KEY"
    assert "MEALIE_BASE" not in flagged and "OVERSEERR_URL" not in flagged


def test_production_env_in_old_names_still_wires_integrations(monkeypatch):
    """The zero-break guarantee: a .env written entirely pre-convention resolves."""
    monkeypatch.setenv("GROCY_URL", "http://grocy.example/")
    monkeypatch.setenv("GROCY_API_KEY", "old-key")
    monkeypatch.setenv("HOME_ASSISTANT_URL", "http://ha.example")
    monkeypatch.setenv("HOME_ASSISTANT_TOKEN", "old-token")
    assert ig.integration("grocy") == {"url": "http://grocy.example", "api_key": "old-key"}
    ha = ig.integration("home_assistant")
    assert ha == {"url": "http://ha.example", "token": "old-token"}
    # old-name envs still pin their fields against runtime edits
    entry = ig._entry("grocy", ist.load())
    assert all(f["env_locked"] for f in entry["fields"])
