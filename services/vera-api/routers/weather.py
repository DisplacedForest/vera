"""Weather watch — proactive severe-weather pre-warnings as a Pulse card.

Pulls the forecast from Open-Meteo (free, no key) in the configured TEMPERATURE_UNIT,
flags severe conditions in the next few days, and if anything's worth warning about,
has Vera write a short pre-warning card and injects it into the Pulse folder.
Zero-floor: calm forecast = no card. The scheduler polls this a few times a day
separately from the morning Pulse run, so big storms surface early.
"""

import hashlib
import json
import os
import time
from datetime import datetime

import aiohttp
from fastapi import APIRouter
from pydantic import BaseModel

from . import units
from .pulse import DEFAULT_FOLDER, SUMMARY_SYS, TZ, _inject, _vera, store
from .persona import home_region_is_us, location, owner, voiced

router = APIRouter()


def _coord(name: str) -> float | None:
    """Home coordinate from env; None when unset so the vein reports unconfigured
    instead of silently watching someone else's sky."""
    v = os.environ.get(name, "").strip()
    try:
        return float(v) if v else None
    except ValueError:
        return None


LAT = _coord("WEATHER_LAT")
LON = _coord("WEATHER_LON")
TZ_NAME = os.environ.get("HOME_TZ", "UTC")
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
# The card's "full forecast" link — a URL template with {lat}/{lon} placeholders. Unset, US
# homes get the NWS map link; elsewhere the card simply carries no forecast link (NWS is US-only).
FORECAST_URL_TMPL = os.environ.get("WEATHER_FORECAST_URL", "").strip()


def _provider_url() -> str:
    """The forecast endpoint (any Open-Meteo-compatible API): the weather vein's provider
    slot, defaulting to the public instance."""
    from . import pulse_veins
    return pulse_veins.provider_values("weather").get("forecast_url") or OPEN_METEO


def _vein_unit() -> tuple[str, str]:
    """(unit, label) for this run: the vein's unit option (store > TEMPERATURE_UNIT env >
    fahrenheit)."""
    from . import pulse_veins
    u = str(pulse_veins.option_values("weather").get("unit") or "").strip().lower()
    if not u.startswith("c") and not u.startswith("f"):
        u = units.unit()
    u = "celsius" if u.startswith("c") else "fahrenheit"
    return u, ("C" if u == "celsius" else "F")

# Open-Meteo WMO weather codes worth a heads-up
SEVERE_CODES = {
    65: "heavy rain", 67: "heavy freezing rain", 75: "heavy snowfall",
    82: "violent rain showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}

# Full WMO code -> short condition text, for the live "current conditions" chip label.
WMO_DESC = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Violent showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm, hail", 99: "Severe thunderstorm",
}

# Current-conditions cache: the vein endpoint is polled often; Open-Meteo is refreshed at
# most every 10 min. None on failure -> the chip shows "N/A" (live-data rule: no hardcoded default).
_current = {"ts": 0.0, "label": None}
_CURRENT_TTL = 600


async def current_label(lat: float | None = None, lon: float | None = None) -> str | None:
    """Live current conditions as a short chip label, e.g. "72F partly cloudy". Cached ~10 min;
    returns None if Open-Meteo is unreachable so the caller can show an explicit N/A state."""
    now = time.time()
    if _current["label"] is not None and now - _current["ts"] < _CURRENT_TTL:
        return _current["label"]
    lat = lat if lat is not None else LAT
    lon = lon if lon is not None else LON
    if lat is None or lon is None:
        return None  # location unconfigured -> explicit N/A, never a made-up sky
    u, lbl = _vein_unit()
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,weather_code",
        "timezone": TZ_NAME,
        "temperature_unit": u,
        "wind_speed_unit": "mph",
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(_provider_url(), params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
                data = await r.json()
        cur = data.get("current") or {}
        temp = cur.get("temperature_2m")
        desc = WMO_DESC.get(cur.get("weather_code"), "")
        if temp is None:
            return _current["label"]  # keep last good value rather than blanking on a partial response
        label = f"{temp:.0f}°{lbl}" + (f" {desc.lower()}" if desc else "")
        _current.update(ts=now, label=label)
        return label
    except Exception:
        return _current["label"]  # stale-but-real if we ever had one, else None -> N/A


# Weather is a SINGLE living card. The visible forecast's signature is stored in the card's `category`
# field so a re-poll of an unchanged forecast can skip regeneration and update in place, not duplicate.
def _concerns_sig(concerns) -> str:
    """Stable hash of the *displayed* forecast (rounded to what the card shows), so trivial
    sub-degree drift between polls doesn't count as a change but a real shift does."""
    norm = [{
        "date": c.get("date"),
        "flags": sorted(c.get("flags") or []),
        "high": round(c["high"]) if c.get("high") is not None else None,
        "low": round(c["low"]) if c.get("low") is not None else None,
        "gust": round(c["gust_mph"]) if c.get("gust_mph") is not None else None,
        "precip": round(c["precip_pct"]) if c.get("precip_pct") is not None else None,
    } for c in concerns]
    return hashlib.sha1(json.dumps(norm, sort_keys=True).encode()).hexdigest()[:12]


def resolve_thresholds(unit: str, heat: float | None, freeze: float | None) -> tuple[float, float]:
    """The heat/freeze flag thresholds, interpreted in the configured TEMPERATURE_UNIT.
    Unset thresholds default to the same physical bar in either unit (100F/38C extreme heat,
    15F/-9C hard freeze)."""
    celsius = unit == "celsius"
    if heat is None:
        heat = 38.0 if celsius else 100.0
    if freeze is None:
        freeze = -9.0 if celsius else 15.0
    return heat, freeze


class WeatherRequest(BaseModel):
    latitude: float | None = None
    longitude: float | None = None
    forecast_days: int = 3
    # Threshold precedence: an explicit request value > the weather vein's stored option >
    # the default (45 mph gusts; heat/freeze are unit-appropriate, see resolve_thresholds).
    gust_mph_threshold: float | None = None
    heat_threshold: float | None = None
    freeze_threshold: float | None = None
    pulse_folder_id: str | None = None


def _forecast_sources(lat, lon):
    """The card's 'full forecast' source link (see FORECAST_URL_TMPL); may be empty."""
    if FORECAST_URL_TMPL:
        return [{"n": 1, "title": "Full forecast",
                 "url": FORECAST_URL_TMPL.format(lat=lat, lon=lon)}]
    if home_region_is_us():
        return [{"n": 1, "title": "Full forecast — National Weather Service",
                 "url": f"https://forecast.weather.gov/MapClick.php?lat={lat}&lon={lon}"}]
    return []


@router.post("/weather/check", tags=["weather"])
async def check(req: WeatherRequest):
    from . import pulse_veins
    if not pulse_veins.is_enabled("weather"):
        return {"ok": False, "disabled": True, "detail": pulse_veins.gate_reason("weather")}
    vein_opts = pulse_veins.option_values("weather")
    lat = req.latitude if req.latitude is not None else LAT
    lon = req.longitude if req.longitude is not None else LON
    if lat is None or lon is None:
        return {"ok": False, "configured": False,
                "error": "weather unconfigured — set WEATHER_LAT and WEATHER_LON"}
    folder = req.pulse_folder_id or DEFAULT_FOLDER
    u, lbl = _vein_unit()
    # explicit request values win; else the vein's stored thresholds; else unit-appropriate defaults
    heat_threshold, freeze_threshold = resolve_thresholds(
        u,
        req.heat_threshold if req.heat_threshold is not None else vein_opts.get("heat_threshold"),
        req.freeze_threshold if req.freeze_threshold is not None else vein_opts.get("freeze_threshold"))
    gust_threshold = req.gust_mph_threshold if req.gust_mph_threshold is not None else (
        vein_opts.get("gust_threshold") if vein_opts.get("gust_threshold") is not None else 45.0)
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max,wind_gusts_10m_max",
        "timezone": TZ_NAME,
        "forecast_days": req.forecast_days,
        "temperature_unit": u,
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
    }
    out = {"ok": True, "lat": lat, "lon": lon, "severe": False, "concerns": [], "injected": False}

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(_provider_url(), params=params, timeout=aiohttp.ClientTimeout(total=20)) as r:
                data = await r.json()
    except Exception as e:
        out["ok"] = False
        out["error"] = f"Open-Meteo unreachable: {e}"
        return out

    daily = data.get("daily", {})
    days = daily.get("time", []) or []

    def col(name):
        return daily.get(name) or [None] * len(days)

    codes, gusts = col("weather_code"), col("wind_gusts_10m_max")
    tmaxs, tmins = col("temperature_2m_max"), col("temperature_2m_min")
    pops = col("precipitation_probability_max")

    for i, d in enumerate(days):
        flags = []
        if codes[i] in SEVERE_CODES:
            flags.append(SEVERE_CODES[codes[i]])
        if gusts[i] is not None and gusts[i] >= gust_threshold:
            flags.append(f"wind gusts {gusts[i]:.0f} mph")
        if tmaxs[i] is not None and tmaxs[i] >= heat_threshold:
            flags.append(f"extreme heat {tmaxs[i]:.0f}{lbl}")
        if tmins[i] is not None and tmins[i] <= freeze_threshold:
            flags.append(f"hard freeze {tmins[i]:.0f}{lbl}")
        if flags:
            out["concerns"].append({
                "date": d, "flags": flags,
                "high": tmaxs[i], "low": tmins[i],
                "gust_mph": gusts[i], "precip_pct": pops[i],
            })

    # Weather is ONE living card, not a new entry per poll. Find the active weather card(s).
    existing = [c for c in store.list_cards()
                if c.get("kind") == "weather" and c.get("status") in ("new", "seen")]
    existing.sort(key=lambda c: c.get("created_at") or 0, reverse=True)

    if not out["concerns"]:
        for c in existing:
            store.delete_card(c["id"])  # forecast cleared — retire the watch, don't leave it lingering
        return out  # zero-floor: calm forecast, no card

    out["severe"] = True
    current = existing[0] if existing else None
    for c in existing[1:]:
        store.delete_card(c["id"])  # collapse any legacy duplicates down to one

    sig = _concerns_sig(out["concerns"])
    today = datetime.now(TZ).date().isoformat()
    title = "Weather watch · " + ", ".join(out["concerns"][0]["flags"][:2])
    sources = _forecast_sources(lat, lon)

    # Unchanged forecast: keep the one card, just resurface it (status -> new). No model call.
    if current and current.get("category") == sig:
        store.insert_card({**current, "status": "new", "day": today})
        out["injected"] = True
        out["title"] = current.get("title")
        out["unchanged"] = True
        return out

    # New or changed forecast: (re)compose the card.
    sys = (
        f"Write a short severe-weather pre-warning card for {owner()}. "
        "Lead with the headline risk and WHEN it hits, then 2-3 sentences on what to expect and one practical "
        "prep nudge. GitHub-flavored markdown, no preamble."
    )
    usr = (
        f"Forecast flags for the next {req.forecast_days} days near {location()}:\n"
        + "\n".join(
            f"- {c['date']}: {', '.join(c['flags'])} "
            f"(high {c['high']}{lbl} / low {c['low']}{lbl}, gusts {c['gust_mph']} mph, precip {c['precip_pct']}%)"
            for c in out["concerns"]
        )
    )
    body = (await _vera([{"role": "system", "content": voiced(sys)}, {"role": "user", "content": usr}], temperature=0.4)).strip()
    try:
        summary = (await _vera(
            [{"role": "system", "content": SUMMARY_SYS}, {"role": "user", "content": body[:1500]}],
            temperature=0.3,
        )).strip().strip('"').replace("\n", " ")
    except Exception:
        summary = None

    # Weather lives in its own chip, not the research feed. The forecast signature rides in `category`
    # (off the visible body) so the next poll can detect an unchanged forecast.
    if current:
        # Edit the existing card in place — same id/created_at, refreshed content, resurfaced.
        store.insert_card({**current, "status": "new", "day": today, "title": title,
                           "summary": summary or "", "body": body, "sources": sources,
                           "severity": "alert", "category": sig})
    else:
        await _inject(title, body, kind="weather", severity="alert", summary=summary,
                      sources=sources, category=sig)
    out["injected"] = True
    out["title"] = title
    return out
