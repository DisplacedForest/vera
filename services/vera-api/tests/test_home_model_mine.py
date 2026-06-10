"""Home-model miner unit tests. Standalone — run: python3 tests/test_home_model_mine.py

The point of these tests is the grounding guarantee: the miners recover REAL seeded
patterns with their real specifics, and invent NOTHING from noise.
"""
import os
import random
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routers"))
import home_model_mine as m  # noqa: E402

TZ = "America/Chicago"
_Z = ZoneInfo(TZ)


def ev(entity, new, old, dt, *, etype="state_changed", context=None):
    """Build a home_events-shaped row from a local datetime."""
    return {
        "event_type": etype, "entity_id": entity, "domain": entity.split(".")[0],
        "old_state": old, "new_state": new, "ts": int(dt.timestamp()),
        "attrs": None, "context": context,
    }


def weekdays(start: date, n: int):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def test_temporal_recovers_real_window():
    """light.office -> on at ~07:15 on 18 of 20 weekdays -> one weekday temporal pattern, ~07:1x."""
    days = weekdays(date(2026, 5, 4), 20)  # 20 weekday dates
    events = []
    for i, d in enumerate(days):
        # a daily background event so all 20 weekday dates are in the window (denominator = 20)
        events.append(ev("binary_sensor.hall_motion", "on", "off",
                         datetime(d.year, d.month, d.day, 12, 0, tzinfo=_Z)))
        if i in (3, 11):  # skip 2 days -> 18/20
            continue
        jitter = (i % 5) - 2  # -2..+2 minutes
        events.append(ev("light.office", "on", "off",
                         datetime(d.year, d.month, d.day, 7, 15 + jitter, tzinfo=_Z)))

    pats = m.mine_temporal(events, TZ)
    office = [p for p in pats if p["entity_id"] == "light.office" and p["spec"]["state"] == "on"]
    assert len(office) == 1, f"expected 1 office temporal pattern, got {office}"
    p = office[0]
    assert p["spec"]["day_class"] == "weekday", p["spec"]
    assert p["support_k"] == 18 and p["support_n"] == 20, p
    assert abs(p["consistency"] - 0.9) < 1e-6, p["consistency"]
    assert p["spec"]["median"].startswith("07:1"), p["spec"]["median"]
    assert p["spec"]["spread_min"] <= 5, p["spec"]
    print("ok test_temporal_recovers_real_window:", p["spec"])


def test_sequence_recovers_lag_and_reliability():
    """front_door->on then entry light->on within ~30s on 9 of 10 opens."""
    base = datetime(2026, 5, 4, 18, 0, tzinfo=_Z)
    events = []
    for i in range(10):
        t = base + timedelta(days=i)
        events.append(ev("binary_sensor.front_door", "on", "off", t))
        if i != 4:  # one open with no light -> 9/10
            events.append(ev("light.entry", "on", "off", t + timedelta(seconds=30)))

    pats = m.mine_sequences(events, TZ, min_count=5)
    seq = [p for p in pats if p["entity_id"] == "binary_sensor.front_door"
           and p["peer_id"] == "light.entry"]
    assert len(seq) == 1, f"expected 1 door->light sequence, got {seq}"
    p = seq[0]
    assert p["support_k"] == 9 and p["support_n"] == 10, p
    assert abs(p["consistency"] - 0.9) < 1e-6, p["consistency"]
    assert abs(p["spec"]["median_lag_s"] - 30) <= 1, p["spec"]
    print("ok test_sequence_recovers_lag_and_reliability:", p["spec"])


def test_noise_invents_nothing():
    """Pure random churn must yield NO temporal or sequence patterns — the grounding guarantee."""
    rng = random.Random(42)
    ents = [f"switch.rand_{i}" for i in range(12)]
    start = datetime(2026, 5, 4, 0, 0, tzinfo=_Z)
    events = []
    for _ in range(800):
        e = rng.choice(ents)
        t = start + timedelta(minutes=rng.randint(0, 30 * 24 * 60))
        new = rng.choice(["on", "off"])
        events.append(ev(e, new, "off" if new == "on" else "on", t))

    assert m.mine_temporal(events, TZ) == [], "temporal invented a pattern from noise"
    assert m.mine_sequences(events, TZ, min_count=5) == [], "sequence invented a pattern from noise"
    print("ok test_noise_invents_nothing")


def test_automation_causation_tagging():
    """state_changed carrying an automation's context id -> tagged caused by that automation."""
    events = []
    base = datetime(2026, 5, 4, 6, 0, tzinfo=_Z)
    for i in range(10):
        t = base + timedelta(days=i)
        events.append(ev("automation.morning_lights", "Morning Lights", None, t,
                         etype="automation_triggered", context={"id": f"ctx{i}"}))
        # the light change it caused carries the automation's context as parent_id
        events.append(ev("light.kitchen", "on", "off", t + timedelta(seconds=1),
                         context={"id": f"child{i}", "parent_id": f"ctx{i}"}))
    # one manual kitchen change (no automation context)
    events.append(ev("light.kitchen", "on", "off", base + timedelta(days=11),
                     context={"id": "manual"}))

    caused = m.automation_causation(events)
    assert "light.kitchen" in caused, caused
    frac, cause = caused["light.kitchen"]
    assert cause == "automation.morning_lights", cause
    assert frac >= 0.6, frac  # 10 of 11 automation-caused
    print("ok test_automation_causation_tagging:", round(frac, 3), cause)


def test_conditional_presence():
    """vacuum.start happens consistently when nobody is home."""
    events = []
    start = datetime(2026, 5, 4, 9, 0, tzinfo=_Z)
    for i in range(8):
        t = start + timedelta(days=i)
        # leave at 08:00, come home at 17:00 -> away during 09:00 run
        events.append(ev("person.alex", "not_home", "home",
                         t.replace(hour=8)))
        events.append(ev("vacuum.downstairs", "cleaning", "docked", t))
        events.append(ev("person.alex", "home", "not_home", t.replace(hour=17)))

    pats = m.mine_conditional(events, TZ, min_count=5)
    cond = [p for p in pats if p["entity_id"] == "vacuum.downstairs"
            and "nobody is home" in p["spec"]["condition"]]
    assert cond, f"expected a 'nobody is home' conditional, got {pats}"
    print("ok test_conditional_presence:", cond[0]["spec"])


def test_conditional_numeric_rejects_counter_accepts_selective():
    """A monotonic counter sharing a name token must NOT yield a conditional (the storage_used
    false-positive the live dry-run exposed); a genuinely selective high-quartile reading should."""
    start = datetime(2026, 5, 4, 9, 0, tzinfo=_Z)
    events = []
    # monotonic counter, climbing all window — interleaved with the purifier events
    for i in range(60):
        t = start + timedelta(minutes=i * 10)
        events.append(ev("sensor.bedroom_storage_used", str(1000 + i * 50),
                         str(1000 + (i - 1) * 50), t, etype="state_changed"))
    # a real PM2.5 sensor that swings; mostly low, spikes high right before the purifier kicks up
    pm_low, pm_high = 5.0, 40.0
    for i in range(40):
        t = start + timedelta(minutes=i * 13)
        events.append(ev("sensor.bedroom_pm25", str(pm_low), str(pm_low), t))
    for i in range(8):  # 8 purifier->high events, each preceded by a high pm2.5 reading
        t = start + timedelta(hours=2 + i * 2)
        events.append(ev("sensor.bedroom_pm25", str(pm_high), str(pm_low), t - timedelta(seconds=30)))
        events.append(ev("fan.bedroom_purifier", "high", "auto", t))

    pats = m.mine_conditional(events, TZ, min_count=5)
    counters = [p for p in pats if p.get("peer_id") == "sensor.bedroom_storage_used"]
    assert not counters, f"monotonic counter leaked a conditional: {counters}"
    pm = [p for p in pats if p["entity_id"] == "fan.bedroom_purifier"
          and p.get("peer_id") == "sensor.bedroom_pm25"]
    assert pm, f"expected a real pm2.5 conditional, got {pats}"
    assert ">" in pm[0]["spec"]["condition"], pm[0]["spec"]
    print("ok test_conditional_numeric_rejects_counter_accepts_selective:", pm[0]["spec"])


def test_conditional_skips_derived_same_object_sensor():
    """A binary_sensor conditioned on the numeric sensor it's literally derived from (same object
    id) is tautological and must be skipped — the dishwasher_power case from the live model."""
    start = datetime(2026, 5, 4, 18, 0, tzinfo=_Z)
    events = []
    for i in range(8):
        t = start + timedelta(hours=i * 3)
        events.append(ev("sensor.dishwasher_power", "5", "5", t - timedelta(minutes=5)))
        events.append(ev("sensor.dishwasher_power", "800", "5", t - timedelta(seconds=10)))
        events.append(ev("binary_sensor.dishwasher_power", "on", "off", t))
    pats = m.mine_conditional(events, TZ, min_count=5)
    derived = [p for p in pats if p["entity_id"] == "binary_sensor.dishwasher_power"
               and p.get("peer_id") == "sensor.dishwasher_power"]
    assert not derived, f"tautological same-object conditional leaked: {derived}"
    print("ok test_conditional_skips_derived_same_object_sensor")


def test_conditional_no_relation_on_measurement_word_only():
    """fridge_power and rack_gpu_power share only the word 'power' — not a relationship. The
    live model produced this false 'fridge off when GPU power > 6.9'; it must not recur."""
    start = datetime(2026, 5, 4, 0, 0, tzinfo=_Z)
    events = []
    rng = random.Random(7)
    for i in range(120):  # GPU power wandering, mostly nonzero
        t = start + timedelta(minutes=i * 12)
        events.append(ev("sensor.rack_gpu_power", str(round(rng.uniform(5, 300), 1)),
                         "0", t, etype="state_changed"))
    for i in range(8):  # fridge compressor cycling, unrelated
        t = start + timedelta(hours=i * 2 + 1)
        events.append(ev("binary_sensor.fridge_power", "off", "on", t))
    pats = m.mine_conditional(events, TZ, min_count=5)
    bogus = [p for p in pats if p.get("peer_id") == "sensor.rack_gpu_power"]
    assert not bogus, f"matched unrelated devices on 'power' alone: {bogus}"
    print("ok test_conditional_no_relation_on_measurement_word_only")


if __name__ == "__main__":
    test_temporal_recovers_real_window()
    test_sequence_recovers_lag_and_reliability()
    test_noise_invents_nothing()
    test_automation_causation_tagging()
    test_conditional_presence()
    test_conditional_numeric_rejects_counter_accepts_selective()
    test_conditional_skips_derived_same_object_sensor()
    test_conditional_no_relation_on_measurement_word_only()
    print("\nall home_model_mine tests passed")
