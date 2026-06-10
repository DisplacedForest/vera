"""Active-hour extraction tests. Standalone — run: python3 tests/test_home_extract.py"""
import os
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routers"))
import rhythm_store as rs  # noqa: E402

TZ = ZoneInfo("America/Chicago")
ON = lambda s: s == "on"  # noqa: E731


def test_single_hour():
    tl = [("2026-06-01T06:50:00-05:00", "off"),
          ("2026-06-01T07:10:00-05:00", "on"),
          ("2026-06-01T07:40:00-05:00", "off")]
    assert rs.hours_active(tl, ON, TZ, "2026-06-01") == {7}


def test_spans_multiple_hours():
    tl = [("2026-06-01T07:30:00-05:00", "on"),
          ("2026-06-01T09:15:00-05:00", "off")]
    assert rs.hours_active(tl, ON, TZ, "2026-06-01") == {7, 8, 9}


def test_carryover_from_before_window():
    # already "on" the night before and never changes -> on all day
    tl = [("2026-05-31T23:00:00-05:00", "on")]
    assert rs.hours_active(tl, ON, TZ, "2026-06-01") == set(range(24))


def test_no_pre_window_sample_is_inactive_until_first():
    # first sample mid-day; nothing before it should be marked active
    tl = [("2026-06-01T12:30:00-05:00", "on"),
          ("2026-06-01T12:45:00-05:00", "off")]
    assert rs.hours_active(tl, ON, TZ, "2026-06-01") == {12}


def test_empty():
    assert rs.hours_active([], ON, TZ, "2026-06-01") == set()


if __name__ == "__main__":
    test_single_hour()
    test_spans_multiple_hours()
    test_carryover_from_before_window()
    test_no_pre_window_sample_is_inactive_until_first()
    test_empty()
    print("OK")
