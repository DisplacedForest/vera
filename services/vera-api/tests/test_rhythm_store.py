"""Rhythm store unit tests. Standalone — run: python3 tests/test_rhythm_store.py"""
import os
import sys
import tempfile

os.environ["RHYTHM_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "r.db")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routers"))
import rhythm_store as rs  # noqa: E402

META = {"light.kitchen": {"domain": "light", "friendly_name": "Kitchen Light", "active_def": "on"}}


def test_record_day_buckets():
    # 2026-06-01 is a Monday -> dow 0
    assert rs.record_day("2026-06-01", {"light.kitchen": {7, 8, 18}}, META) is True
    assert rs.prob("light.kitchen", 0, 7) == (1.0, 1.0)
    assert rs.prob("light.kitchen", 0, 9) == (0.0, 1.0)
    # idempotent: re-recording the same day is a no-op
    assert rs.record_day("2026-06-01", {"light.kitchen": {7}}, META) is False
    assert rs.prob("light.kitchen", 0, 7) == (1.0, 1.0)


def test_accumulate_and_prob():
    # 11 more Mondays active at h=7 only -> 12 observed total
    for d in ["2026-06-08", "2026-06-15", "2026-06-22", "2026-06-29", "2026-07-06",
              "2026-07-13", "2026-07-20", "2026-07-27", "2026-08-03", "2026-08-10", "2026-08-17"]:
        rs.record_day(d, {"light.kitchen": {7}}, META)
    assert rs.prob("light.kitchen", 0, 7) == (1.0, 12.0)   # active every Monday at 7
    assert rs.prob("light.kitchen", 0, 9) == (0.0, 12.0)   # never at 9
    # unseen bucket (different dow)
    assert rs.prob("light.kitchen", 3, 7) == (0.0, 0.0)


def test_baseline():
    b = rs.baseline("light.kitchen")
    assert b[(0, 7)] == (1.0, 12.0)
    assert b[(0, 9)] == (0.0, 12.0)


def test_detect_pure():
    # expected-but-absent: usually on, not on now
    assert rs.detect("x", p=1.0, observed=12, active_now=False)["kind"] == "absent"
    assert rs.detect("x", p=1.0, observed=12, active_now=True) is None
    # unexpected-presence: never on, on now
    assert rs.detect("x", p=0.0, observed=12, active_now=True)["kind"] == "unexpected"
    assert rs.detect("x", p=0.0, observed=12, active_now=False) is None
    # below confidence floor -> never fires
    assert rs.detect("x", p=1.0, observed=5, active_now=False) is None
    assert rs.detect("x", p=0.0, observed=5, active_now=True) is None


if __name__ == "__main__":
    test_record_day_buckets()
    test_accumulate_and_prob()
    test_baseline()
    test_detect_pure()
    print("OK")
