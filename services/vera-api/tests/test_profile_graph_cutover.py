"""Legacy-store write-cutover — once the Profile Graph is the write target, the old interest
ACCRUAL writes stop, so the deprecated stores stop diverging. A single reversible env flag
(PROFILE_GRAPH_CUTOVER) gates it; reads are never affected, and cooldown/profile-config writes
stay until the layers that read them are retired. Run under pytest."""
import os

import pytest

from routers import user_profile_store as up
from routers import vera_interests_store as vi


@pytest.fixture(autouse=True)
def _fresh(tmp_path):
    vi.DB_PATH = os.path.join(str(tmp_path), "interests.db")
    vi.init()
    up.DB_PATH = os.path.join(str(tmp_path), "profiles.db")
    up.init()
    yield


def test_cutover_noops_interest_accrual(monkeypatch):
    monkeypatch.setenv("PROFILE_GRAPH_CUTOVER", "1")
    vi.observe("winemaking", source="chat")
    up.observe("user-1", "Nottingham Forest")
    assert vi.all_interests() == []                 # nothing accrued into the legacy store
    assert up.interests("user-1") == []


def test_legacy_accrual_resumes_when_cutover_off(monkeypatch):
    monkeypatch.delenv("PROFILE_GRAPH_CUTOVER", raising=False)
    vi.observe("winemaking", source="chat")
    up.observe("user-1", "Nottingham Forest")
    assert any(i["topic"] == "winemaking" for i in vi.all_interests())
    assert any(i["topic"] == "Nottingham Forest" for i in up.interests("user-1"))


def test_cutover_leaves_reads_and_cooldown_intact(monkeypatch):
    # seed BEFORE the cutover, then engage it: existing data still reads, touch still works
    vi.observe("winemaking", salience_bump=5.0)
    monkeypatch.setenv("PROFILE_GRAPH_CUTOVER", "1")
    assert any(i["topic"] == "winemaking" for i in vi.all_interests())   # reads unaffected
    vi.touch("winemaking")                                              # cooldown bookkeeping still runs
    assert "winemaking" in vi.cooled(["winemaking"])