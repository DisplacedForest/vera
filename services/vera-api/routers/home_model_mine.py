"""Home-model miners — pure, deterministic pattern derivation over home_events rows.

events-in -> pattern-dicts-out. No HA, no LLM, no DB, no wall-clock. Every surfaced pattern is
grounded in the event log: it carries the REAL timing / conditions / reliability and a
support count, and the miners never invent — pure noise yields nothing. This module is the
testable core of the home model; the router just orchestrates mine -> HA cross-ref -> narrate.

Four miners, one per pattern family:
  mine_temporal   — entity -> state at consistent times, with the real time window (not a 7x24 bucket)
  mine_sequences  — A reliably followed by B within Δt, with the real median lag
  mine_conditional— X happens when a presence / numeric-threshold condition holds
  mine_numeric    — how a numeric entity actually moves over the day (curve + cycle), at fidelity
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

DEFAULT_TZ = os.environ.get("HOME_TZ", "UTC")

# Domains whose state strings are discrete/categorical — the temporal/sequence/conditional miners
# operate on these. Pure-numeric sensor.* go to the numeric miner instead.
DISCRETE_DOMAINS = {
    "light", "switch", "fan", "lock", "cover", "media_player", "climate", "binary_sensor",
    "person", "device_tracker", "vacuum", "input_boolean", "scene", "humidifier",
    "alarm_control_panel", "remote", "water_heater",
}
NULL_STATES = {"unknown", "unavailable", "none", "", None}
# Name tokens too generic to imply two entities are related (used by the conditional miner).
# Two entities are "related" when they share a LOCATION/DEVICE token (bedroom, fridge) — never a
# measured-quantity word (power, temperature). Matching on the quantity falsely links unrelated
# devices ("fridge_power" vs "washer_power" share only "power"), so those are stopworded too.
STOPWORD_TOKENS = {
    "sensor", "binary", "the", "home", "state", "status", "mode", "level",
    "switch", "light", "lights", "room", "device", "tracker", "person",
    # measured quantities — shared by unrelated devices, so not evidence of a relationship
    "power", "temperature", "temp", "humidity", "battery", "voltage", "current", "energy",
    "signal", "co2", "pm25", "pressure", "illuminance", "lux", "watts", "volts", "amps",
    "percent", "consumption", "usage",
}
# Name tokens that mark a monotonic counter / accumulator — "value > its median" is trivially true
# for any later event, so a threshold against one is time, not a real condition. Skip them.
COUNTER_TOKENS = {
    "storage", "used", "uptime", "total", "energy", "count", "counter", "bytes",
    "seconds", "sum", "kwh", "consumption", "lifetime", "odometer",
}


def _dom(e: dict) -> str:
    return e.get("domain") or (e["entity_id"].split(".", 1)[0] if e.get("entity_id") else "")


def _local(ts: int, tz: str) -> datetime:
    return datetime.fromtimestamp(ts, ZoneInfo(tz))


def _pct(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolated percentile (q in [0,1]) over a pre-sorted list."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    frac = pos - lo
    if lo + 1 >= len(sorted_vals):
        return sorted_vals[lo]
    return sorted_vals[lo] + frac * (sorted_vals[lo + 1] - sorted_vals[lo])


def _median(vals: list[float]) -> float:
    s = sorted(vals)
    return _pct(s, 0.5)


def _hhmm(sod: float) -> str:
    sod = int(sod) % 86400
    return f"{sod // 3600:02d}:{(sod % 3600) // 60:02d}"


def _as_float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _transitions(events: list[dict]) -> list[dict]:
    """Discrete state_changed events that are real transitions to a non-null state, ts-sorted."""
    out = [
        e for e in events
        if e.get("event_type") == "state_changed"
        and _dom(e) in DISCRETE_DOMAINS
        and e.get("new_state") not in NULL_STATES
        and e.get("old_state") != e.get("new_state")
    ]
    out.sort(key=lambda e: e["ts"])
    return out


def _window_dates(events: list[dict], tz: str):
    """All distinct local dates present, plus weekday/weekend day counts (the consistency denominators)."""
    dates = {_local(e["ts"], tz).date() for e in events if e.get("ts")}
    weekday = sum(1 for d in dates if d.weekday() < 5)
    weekend = sum(1 for d in dates if d.weekday() >= 5)
    return dates, weekday, weekend


# --------------------------------------------------------------------------------------
# 1. Temporal regularities
# --------------------------------------------------------------------------------------
def mine_temporal(events, tz: str = DEFAULT_TZ, *, min_days: int = 5, max_spread_s: int = 900,
                  min_consistency: float = 0.6, cluster_gap_s: int = 1800) -> list[dict]:
    """entity -> state events that cluster at a consistent time-of-day on a consistent day-class.

    Keeps the REAL time window (p10..p90, e.g. "07:13-07:18") — never a coarse bucket.
    consistency = (distinct days the event landed in the cluster) / (eligible days of that class).
    """
    trans = _transitions(events)
    if not trans:
        return []
    all_dates, n_weekday, n_weekend = _window_dates(events, tz)
    n_all = len(all_dates)

    occ = defaultdict(list)  # (entity, state) -> [(date, dow, sod)]
    for e in trans:
        dt = _local(e["ts"], tz)
        occ[(e["entity_id"], e["new_state"])].append(
            (dt.date(), dt.weekday(), dt.hour * 3600 + dt.minute * 60 + dt.second)
        )

    out = []
    for (entity, state), pts in occ.items():
        if len(pts) < min_days:
            continue
        pts.sort(key=lambda p: p[2])
        # gap-cluster on time-of-day (wrap across midnight is rare for home rhythms; ignored in v1)
        clusters, cur = [], [pts[0]]
        for prev, p in zip(pts, pts[1:]):
            if p[2] - prev[2] > cluster_gap_s:
                clusters.append(cur)
                cur = []
            cur.append(p)
        clusters.append(cur)

        for cl in clusters:
            sods = sorted(p[2] for p in cl)
            dows = [p[1] for p in cl]
            wd_frac = sum(1 for d in dows if d < 5) / len(dows)
            if wd_frac >= 0.8:
                day_class, denom = "weekday", n_weekday
                days = {p[0] for p in cl if p[0].weekday() < 5}
            elif wd_frac <= 0.2:
                day_class, denom = "weekend", n_weekend
                days = {p[0] for p in cl if p[0].weekday() >= 5}
            else:
                day_class, denom = "any day", n_all
                days = {p[0] for p in cl}
            k = len(days)
            if denom < min_days or k < min_days:
                continue
            p10, p90 = _pct(sods, 0.1), _pct(sods, 0.9)
            spread = p90 - p10
            if spread > max_spread_s:
                continue
            consistency = round(k / denom, 3)
            if consistency < min_consistency:
                continue
            # tightness factor lightly rewards a narrow window; consistency stays the headline.
            score = round(consistency * (1 - min(spread, max_spread_s) / (2 * max_spread_s)), 3)
            out.append({
                "kind": "temporal",
                "entity_id": entity,
                "peer_id": None,
                "consistency": consistency,
                "score": score,
                "support_k": k,
                "support_n": denom,
                "spec": {
                    "state": state,
                    "window": f"{_hhmm(p10)}–{_hhmm(p90)}",
                    "median": _hhmm(_median(sods)),
                    "spread_min": round(spread / 60, 1),
                    "day_class": day_class,
                    "frequency": f"{k}/{denom}",
                },
            })
    return out


# --------------------------------------------------------------------------------------
# 2. Sequences / correlations
# --------------------------------------------------------------------------------------
def mine_sequences(events, tz: str = DEFAULT_TZ, *, dt_max_s: int = 120, min_count: int = 5,
                   min_reliability: float = 0.7) -> list[dict]:
    """A=(entity->state) reliably followed by B=(entity->state) within dt_max_s, with real median lag."""
    trans = _transitions(events)
    n = len(trans)
    if n < min_count:
        return []
    by_a = defaultdict(list)  # (entity, state) -> [indices]
    for i, e in enumerate(trans):
        by_a[(e["entity_id"], e["new_state"])].append(i)

    out = []
    for a_key, idxs in by_a.items():
        a_total = len(idxs)
        if a_total < min_count:
            continue
        follow_lags = defaultdict(list)  # b_key -> [lag per A-occurrence where it first appeared]
        for i in idxs:
            ta = trans[i]["ts"]
            seen = {}
            j = i + 1
            while j < n and trans[j]["ts"] - ta <= dt_max_s:
                b_key = (trans[j]["entity_id"], trans[j]["new_state"])
                if b_key[0] != a_key[0] and b_key not in seen:
                    seen[b_key] = trans[j]["ts"] - ta
                j += 1
            for b_key, lag in seen.items():
                follow_lags[b_key].append(lag)
        for b_key, lags in follow_lags.items():
            cnt = len(lags)
            rel = cnt / a_total
            if cnt < min_count or rel < min_reliability:
                continue
            out.append({
                "kind": "sequence",
                "entity_id": a_key[0],
                "peer_id": b_key[0],
                "consistency": round(rel, 3),
                "score": round(rel, 3),
                "support_k": cnt,
                "support_n": a_total,
                "spec": {
                    "trigger": f"{a_key[0]} → {a_key[1]}",
                    "follow": f"{b_key[0]} → {b_key[1]}",
                    "median_lag_s": int(_median(lags)),
                    "reliability": round(rel, 3),
                    "count": cnt,
                    "of": a_total,
                },
            })
    return out


# --------------------------------------------------------------------------------------
# 3. Conditional triggers (presence + numeric-threshold; conservative to avoid confabulation)
# --------------------------------------------------------------------------------------
def _name_tokens(entity_id: str) -> set[str]:
    parts = re.split(r"[._\s]+", entity_id.lower())
    return {p for p in parts if len(p) >= 3 and p not in STOPWORD_TOKENS}


def _numeric_series(events, tz: str):
    """{entity_id: [(ts, value)]} for numeric sensor.* entities, ts-sorted."""
    series = defaultdict(list)
    for e in events:
        if e.get("event_type") != "state_changed":
            continue
        if _dom(e) != "sensor":
            continue
        v = _as_float(e.get("new_state"))
        if v is None:
            continue
        series[e["entity_id"]].append((e["ts"], v))
    for s in series.values():
        s.sort(key=lambda p: p[0])
    return series


def _latest_before(series_pts, ts):
    """Latest (ts,val) at or before ts via linear scan back from the end of a sorted list."""
    lo, hi, ans = 0, len(series_pts) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if series_pts[mid][0] <= ts:
            ans = series_pts[mid][1]
            lo = mid + 1
        else:
            hi = mid - 1
    return ans


def mine_conditional(events, tz: str = DEFAULT_TZ, *, min_count: int = 5, min_frac: float = 0.85) -> list[dict]:
    """X=(entity->state) consistently coincides with a presence or numeric-threshold condition.

    Presence: anyone-home at event time (only emitted when both home AND away periods exist in the
    window, so the condition is non-trivial). Numeric: a sensor that shares a name token with X
    (cheap grounded relevance filter) sits consistently on one side of its own window-wide median.
    """
    trans = _transitions(events)
    if not trans:
        return []

    # presence timeline: anyone home? from person.* / device_tracker.* transitions
    presence_pts = []  # (ts, anyone_home_bool)
    pres_states = {}  # presence entity -> current state, replayed in ts order
    pres_entities = {e["entity_id"] for e in trans if _dom(e) in ("person", "device_tracker")}
    if pres_entities:
        for e in sorted((x for x in trans if x["entity_id"] in pres_entities), key=lambda x: x["ts"]):
            pres_states[e["entity_id"]] = e["new_state"]
            anyone = any(v == "home" for v in pres_states.values())
            presence_pts.append((e["ts"], anyone))

    def presence_at(ts):
        val = None
        for t, a in presence_pts:
            if t <= ts:
                val = a
            else:
                break
        return val

    numeric = _numeric_series(events, tz)
    numeric_stats = {}
    for sid, pts in numeric.items():
        vals = [v for _, v in pts]
        if not vals:
            continue
        sv = sorted(vals)
        # monotonicity over NON-equal steps only — a counter moves strictly one way (mono~1.0);
        # a flat-with-spikes sensor has balanced up/down moves (mono~0.5). Counting equal steps
        # would make any mostly-flat sensor look monotonic.
        up = sum(1 for a, b in zip(vals, vals[1:]) if b > a)
        down = sum(1 for a, b in zip(vals, vals[1:]) if b < a)
        moves = up + down
        numeric_stats[sid] = {
            "sv": sv, "median": _pct(sv, 0.5),
            "mono": (max(up, down) / moves) if moves else 0.0,
        }

    # candidate targets: discrete (entity->state) with enough occurrences, excluding presence itself
    targets = defaultdict(list)  # (entity, state) -> [ts]
    for e in trans:
        if e["entity_id"] in pres_entities:
            continue
        targets[(e["entity_id"], e["new_state"])].append(e["ts"])

    have_home = any(a for _, a in presence_pts)
    have_away = any(not a for _, a in presence_pts)

    out = []
    for (entity, state), tss in targets.items():
        if len(tss) < min_count:
            continue

        # (a) presence conditioning — only meaningful if the window contains both home and away time
        if presence_pts and have_home and have_away:
            home_hits = sum(1 for ts in tss if presence_at(ts) is True)
            away_hits = sum(1 for ts in tss if presence_at(ts) is False)
            known = home_hits + away_hits
            if known >= min_count:
                if home_hits / known >= min_frac:
                    out.append(_cond_pattern(entity, state, "someone is home", home_hits, known))
                elif away_hits / known >= min_frac:
                    out.append(_cond_pattern(entity, state, "nobody is home", away_hits, known))

        # (b) numeric-threshold conditioning against name-related sensors. Conservative: the
        # condition must show LIFT — rare across the sensor's own history (base rate <= 0.4) yet
        # consistent at event times — and the sensor must not be a monotonic counter (else
        # "> median" is just "later in time"). This is what separates a real "purifier high when
        # PM2.5 spikes" from a spurious correlation against a climbing counter.
        ttok = _name_tokens(entity)
        ent_obj = entity.split(".", 1)[-1]
        for sid, st in numeric_stats.items():
            if sid == entity or not (ttok & _name_tokens(sid)):
                continue
            # skip the sensor the target is DERIVED from (same object id, e.g.
            # binary_sensor.dishwasher_power_2 is just a threshold on sensor.dishwasher_power_2 —
            # conditioning one on the other is tautological, not a discovered relationship)
            if sid.split(".", 1)[-1] == ent_obj:
                continue
            if st["mono"] >= 0.85 or _name_tokens(sid) & COUNTER_TOKENS:
                continue
            vals = [v for v in (_latest_before(numeric[sid], ts) for ts in tss) if v is not None]
            if len(vals) < min_count:
                continue
            n, sv = len(vals), st["sv"]
            hi_thr, lo_thr = min(vals), max(vals)  # every event is >= hi_thr (and <= lo_thr)
            base_hi = sum(1 for v in sv if v >= hi_thr) / len(sv)  # how often the sensor is "high" at all
            base_lo = sum(1 for v in sv if v <= lo_thr) / len(sv)
            if hi_thr > st["median"] and base_hi <= 0.4:
                out.append(_cond_pattern(entity, state, f"{sid} > ~{round(hi_thr, 1)}", n, n, peer=sid))
            elif lo_thr < st["median"] and base_lo <= 0.4:
                out.append(_cond_pattern(entity, state, f"{sid} < ~{round(lo_thr, 1)}", n, n, peer=sid))
    return out


def _cond_pattern(entity, state, condition, k, n, peer=None):
    frac = round(k / n, 3)
    return {
        "kind": "conditional",
        "entity_id": entity,
        "peer_id": peer,
        "consistency": frac,
        "score": frac,
        "support_k": k,
        "support_n": n,
        "spec": {
            "event": f"{entity} → {state}",
            "condition": condition,
            "holds": f"{k}/{n}",
        },
    }


# --------------------------------------------------------------------------------------
# 4. Numeric value behavior
# --------------------------------------------------------------------------------------
def mine_numeric(events, tz: str = DEFAULT_TZ, *, min_readings: int = 50, min_days: int = 5,
                 cycle_min_crossings: int = 8) -> list[dict]:
    """How a numeric sensor moves over the day: the real hourly distribution + cycle detection.

    This summarizes a *continuous* signal (you can't list every reading) — it is NOT the earlier
    active/inactive hourly-bucket mistake, which discarded discrete-event exact times. Here we keep the
    real per-hour median/p10/p90 curve and the detected on/off cycle period.
    """
    series = _numeric_series(events, tz)
    out = []
    for eid, pts in series.items():
        if len(pts) < min_readings:
            continue
        days = {_local(ts, tz).date() for ts, _ in pts}
        if len(days) < min_days:
            continue
        by_hour = defaultdict(list)
        for ts, v in pts:
            by_hour[_local(ts, tz).hour].append(v)
        hourly = []
        rel_spreads = []
        for h in range(24):
            hv = sorted(by_hour.get(h, []))
            if not hv:
                continue
            med, p10, p90 = _median(hv), _pct(hv, 0.1), _pct(hv, 0.9)
            hourly.append({"h": h, "median": round(med, 2), "p10": round(p10, 2), "p90": round(p90, 2)})
            if med:
                rel_spreads.append(min((p90 - p10) / abs(med), 1.0))
        allv = sorted(v for _, v in pts)
        overall_med = _median(allv)

        # cycle detection: count median-crossings; a steady oscillation -> period ~ 2*median gap
        crossings = []
        prev_side = None
        for ts, v in pts:
            side = v >= overall_med
            if prev_side is not None and side != prev_side:
                crossings.append(ts)
            prev_side = side
        cycle = None
        if len(crossings) >= cycle_min_crossings:
            gaps = [b - a for a, b in zip(crossings, crossings[1:]) if b > a]
            if gaps:
                cycle = {"detected": True, "period_s": int(_median(gaps) * 2), "crossings": len(crossings)}

        tightness = round(1 - (sum(rel_spreads) / len(rel_spreads)), 3) if rel_spreads else 0.0
        out.append({
            "kind": "numeric",
            "entity_id": eid,
            "peer_id": None,
            "consistency": max(tightness, 0.0),
            "score": max(tightness, 0.0),
            "support_k": len(days),
            "support_n": len(pts),
            "spec": {
                "overall": {"median": round(overall_med, 2), "min": round(allv[0], 2),
                            "max": round(allv[-1], 2)},
                "hourly": hourly,
                "cycle": cycle,
                "readings": len(pts),
                "days": len(days),
            },
        })
    return out


def automation_causation(events) -> dict:
    """From the event log ALONE: entity -> (fraction of its changes caused by an automation/script,
    the causing automation's entity_id). HA stamps each automation/script fire with a context id;
    the state_changed events it causes carry that id as context.id or context.parent_id. This is the
    strongest, fully-grounded already-automated signal — no config guessing.
    """
    auto_ctx = {}  # context id -> automation/script entity_id
    for e in events:
        if e.get("event_type") in ("automation_triggered", "script_started"):
            cid = (e.get("context") or {}).get("id")
            if cid:
                auto_ctx[cid] = e.get("entity_id") or e.get("new_state")
    tally = defaultdict(lambda: [0, 0, defaultdict(int)])  # entity -> [caused, total, name_counts]
    for e in events:
        if e.get("event_type") != "state_changed":
            continue
        t = tally[e["entity_id"]]
        t[1] += 1
        ctx = e.get("context") or {}
        cause = auto_ctx.get(ctx.get("id")) or auto_ctx.get(ctx.get("parent_id"))
        if cause:
            t[0] += 1
            t[2][cause] += 1
    out = {}
    for ent, (caused, total, names) in tally.items():
        if total and caused:
            out[ent] = (caused / total, max(names, key=names.get))
    return out


def mine_all(events, tz: str = DEFAULT_TZ) -> list[dict]:
    """Run all four miners over the event window and return the combined pattern list."""
    return (
        mine_temporal(events, tz)
        + mine_sequences(events, tz)
        + mine_conditional(events, tz)
        + mine_numeric(events, tz)
    )
