"""Grid-hour selection for the EIA collector: the newest hour is often provisional —
actual demand published as a copy of the day-ahead forecast (deviation exactly 0) until
the real value lands hours later. The selector must skip that mirror and report the
max-positive-deviation hour over the recent finalized window, so a real load excursion
is never masked by a provisional 0%."""
from routers import signals


def _hours(*pairs):
    """Build a by_period dict from (D, DF) pairs, newest first."""
    return {f"2026-06-12T{23 - i:02d}": {"D": d, "DF": df}
            for i, (d, df) in enumerate(pairs)}


def test_skips_leading_provisional_mirror():
    # newest hour mirrors the forecast exactly (provisional); real hours behind it
    by = _hours((70000, 70000), (68000, 71000), (74000, 70000), (69000, 70000))
    r = signals._grid_hour(by)
    assert r["dev_pct"] == round((74000 / 70000 - 1) * 100, 1)
    assert r["demand_mw"] == 74000


def test_reports_max_positive_dev_not_newest():
    by = _hours((68000, 70000), (75600, 70000), (70700, 70000))
    r = signals._grid_hour(by)
    assert r["demand_mw"] == 75600
    assert r["dev_pct"] == 8.0


def test_window_limits_lookback():
    # an old spike beyond the 6-hour finalized window must not be reported
    by = _hours(*([(69000, 70000)] * 6 + [(80000, 70000)]))
    r = signals._grid_hour(by)
    assert r["demand_mw"] == 69000


def test_all_mirrors_reads_flat_not_error():
    # a genuinely flat read (or long provisional run) reports 0%, never an error
    by = _hours(*([(70000, 70000)] * 8))
    r = signals._grid_hour(by)
    assert r["dev_pct"] == 0.0


def test_incomplete_hours_ignored():
    by = {"2026-06-12T23": {"D": 70000}, "2026-06-12T22": {"DF": 70000},
          "2026-06-12T21": {"D": 73500, "DF": 70000}}
    r = signals._grid_hour(by)
    assert r["demand_mw"] == 73500


def test_no_complete_hours_returns_none():
    assert signals._grid_hour({"2026-06-12T23": {"D": 70000}}) is None
    assert signals._grid_hour({}) is None
