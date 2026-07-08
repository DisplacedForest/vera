import time

import pytest

from routers import vein_engine_store


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(vein_engine_store, "DB_PATH", str(tmp_path / "vein_engine.db"))
    yield


NOW = int(time.time())
WEEK = 7 * 86400


def test_filter_unseen_passthrough_on_empty_store():
    assert vein_engine_store.filter_unseen("w", ["a", "b"], WEEK) == ["a", "b"]


def test_record_seen_suppresses_within_window():
    vein_engine_store.record_seen("w", ["a"])
    assert vein_engine_store.filter_unseen("w", ["a", "b"], WEEK) == ["b"]


def test_seen_is_per_kind():
    vein_engine_store.record_seen("w", ["a"])
    assert vein_engine_store.filter_unseen("other", ["a"], WEEK) == ["a"]


def test_realert_after_decay():
    vein_engine_store.record_seen("w", ["a"], ts=NOW - WEEK - 60)
    assert vein_engine_store.filter_unseen("w", ["a"], WEEK) == ["a"]


def test_record_seen_upserts_last_ts():
    vein_engine_store.record_seen("w", ["a"], ts=NOW - WEEK - 60)
    vein_engine_store.record_seen("w", ["a"], ts=NOW)
    assert vein_engine_store.filter_unseen("w", ["a"], WEEK) == []


def test_prune_on_write_drops_expired_rows():
    vein_engine_store.record_seen("w", ["old"], ts=NOW - WEEK - 60)
    vein_engine_store.record_seen("w", ["fresh"], ts=NOW)
    assert vein_engine_store.seen_count("w") == 1


def test_floor_roundtrip():
    assert vein_engine_store.last_run("w") is None
    vein_engine_store.mark_run("w", ts=NOW)
    assert vein_engine_store.last_run("w") == NOW
    vein_engine_store.mark_run("w", ts=NOW + 5)
    assert vein_engine_store.last_run("w") == NOW + 5
