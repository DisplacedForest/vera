"""Failure-streak escalation tests. Standalone — run: pytest tests/test_scheduler_failcards.py

Repeated scheduler failures must surface as ONE System-vein card (kind=status,
category=vera) — never a stack — and the card must clear the moment the job
recovers. Below the streak threshold, and while the System vein is disabled,
nothing is posted.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from routers import pulse_store  # noqa: E402
from routers import pulse_veins  # noqa: E402
from routers import scheduler  # noqa: E402
from routers import scheduler_store  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler_store, "DB_PATH", str(tmp_path / "scheduler.db"))
    monkeypatch.setattr(pulse_store, "DB_PATH", str(tmp_path / "pulse.db"))
    monkeypatch.setattr(pulse_veins, "is_enabled", lambda kind: True)
    yield


def _vera_cards():
    return [c for c in pulse_store.list_cards()
            if c.get("kind") == "status" and c.get("category") == "vera"]


def _escalate(job_id="healthcheck"):
    asyncio.run(scheduler.escalate_failures(job_id))


def test_streak_posts_one_alert_card():
    for i in range(scheduler.FAIL_CARD_AFTER):
        scheduler_store.record_outcome("healthcheck", False, f"boom {i}")
    _escalate()
    cards = _vera_cards()
    assert len(cards) == 1
    assert "keeps failing" in cards[0]["title"]
    assert cards[0]["severity"] == "alert"
    assert "boom" in cards[0]["body"]
    # Another failing pass never stacks a duplicate.
    scheduler_store.record_outcome("healthcheck", False, "boom again")
    _escalate()
    assert len(_vera_cards()) == 1


def test_below_threshold_stays_quiet():
    for _ in range(scheduler.FAIL_CARD_AFTER - 1):
        scheduler_store.record_outcome("healthcheck", False, "boom")
    _escalate()
    assert _vera_cards() == []


def test_recovery_clears_the_card():
    for _ in range(scheduler.FAIL_CARD_AFTER):
        scheduler_store.record_outcome("healthcheck", False, "boom")
    _escalate()
    assert len(_vera_cards()) == 1
    scheduler_store.record_outcome("healthcheck", True, "ok")
    _escalate()
    assert _vera_cards() == []


def test_disabled_vein_posts_nothing(monkeypatch):
    monkeypatch.setattr(pulse_veins, "is_enabled", lambda kind: False)
    for _ in range(scheduler.FAIL_CARD_AFTER):
        scheduler_store.record_outcome("healthcheck", False, "boom")
    _escalate()
    assert _vera_cards() == []


def test_streaks_are_per_job():
    for _ in range(scheduler.FAIL_CARD_AFTER):
        scheduler_store.record_outcome("healthcheck", False, "boom")
    scheduler_store.record_outcome("weather", False, "drizzle")
    _escalate("weather")
    assert _vera_cards() == []  # weather's streak is 1, healthcheck's card not its business
    _escalate("healthcheck")
    assert len(_vera_cards()) == 1
