"""Weather — the `weather_conditions` vein block plus the live current-conditions chip.

The block pulls the forecast from an Open-Meteo-compatible endpoint in the configured
unit, flags severe conditions in the next few days, and emits at most one standing
situation for the Weather vein; the engine composes the pre-warning card, updates it
in place when the forecast shifts, and retires it when the forecast calms.
"""

import os
import time

import aiohttp
from fastapi import APIRouter

from . import units, vein_engine
from .persona import home_region_is_us, location

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


def _unit_from(value) -> tuple[str, str]:
    u = str(value or "").strip().lower()
    if not u.startswith("c") and not u.startswith("f"):
        u = units.unit()
    u = "celsius" if u.startswith("c") else "fahrenheit"
    return u, ("C" if u == "celsius" else "F")


def _vein_unit() -> tuple[str, str]:
    """(unit, label) for this run: the vein's unit option (store > TEMPERATURE_UNIT env >
    fahrenheit)."""
    from . import pulse_veins
    return _unit_from(pulse_veins.option_values("weather").get("unit"))

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


def _forecast_sources(lat, lon):
    """The card's 'full forecast' source link (see FORECAST_URL_TMPL); may be empty."""
    if FORECAST_URL_TMPL:
        return [{"n": 1, "title": "Full forecast",
                 "url": FORECAST_URL_TMPL.format(lat=lat, lon=lon)}]
    if home_region_is_us():
        return [{"n": 1, "title": "Full forecast (National Weather Service)",
                 "url": f"https://forecast.weather.gov/MapClick.php?lat={lat}&lon={lon}"}]
    return []


async def _fetch_daily(provider: str, lat: float, lon: float, days: int, unit: str) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max,wind_gusts_10m_max",
        "timezone": TZ_NAME,
        "forecast_days": days,
        "temperature_unit": unit,
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
    }
    async with aiohttp.ClientSession() as s:
        async with s.get(provider, params=params, timeout=aiohttp.ClientTimeout(total=20)) as r:
            return await r.json()


def _concern_days(daily: dict, gust_threshold: float, heat_threshold: float,
                  freeze_threshold: float, lbl: str) -> list[dict]:
    days = daily.get("time", []) or []

    def col(name):
        return daily.get(name) or [None] * len(days)

    codes, gusts = col("weather_code"), col("wind_gusts_10m_max")
    tmaxs, tmins = col("temperature_2m_max"), col("temperature_2m_min")
    pops = col("precipitation_probability_max")
    concerns = []
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
            concerns.append({"date": d, "flags": flags, "high": tmaxs[i], "low": tmins[i],
                             "gust_mph": gusts[i], "precip_pct": pops[i]})
    return concerns


def _fmt(v) -> str:
    return f"{v:.0f}" if v is not None else "?"


async def _block_weather_conditions(items, params, ctx):
    if LAT is None or LON is None:
        raise vein_engine.BlockError(
            "weather_conditions", "weather unconfigured. Set WEATHER_LAT and WEATHER_LON")
    opts = ctx.get("options") or {}
    u, lbl = _unit_from(opts.get("unit"))
    heat_threshold, freeze_threshold = resolve_thresholds(
        u, opts.get("heat_threshold"), opts.get("freeze_threshold"))
    gust_threshold = (opts.get("gust_threshold")
                      if opts.get("gust_threshold") is not None else 45.0)
    provider = (ctx.get("providers") or {}).get("forecast_url") or OPEN_METEO
    days = int(params.get("forecast_days", 3))
    try:
        data = await _fetch_daily(provider, LAT, LON, days, u)
    except Exception as e:
        raise vein_engine.BlockError("weather_conditions", f"forecast unreachable: {e}")
    concerns = _concern_days(data.get("daily", {}), gust_threshold,
                             heat_threshold, freeze_threshold, lbl)
    if not concerns:
        return items
    lines = "\n".join(
        f"- {c['date']}: {', '.join(c['flags'])} "
        f"(high {_fmt(c['high'])}{lbl} / low {_fmt(c['low'])}{lbl}, "
        f"gusts {_fmt(c['gust_mph'])} mph, precip {_fmt(c['precip_pct'])}%)"
        for c in concerns)
    item = {"key": "weather:watch",
            "title": "Weather watch · " + ", ".join(concerns[0]["flags"][:2]),
            "content": f"forecast flags for the next {days} days near {location()}:\n{lines}",
            "severity": "alert"}
    sources = _forecast_sources(LAT, LON)
    if sources:
        item["sources"] = sources
    return items + [item]


vein_engine.register("weather_conditions", _block_weather_conditions, monitor=True,
                     describe="emits at most one standing item when the forecast crosses the configured wind, heat, or freeze thresholds; forecast endpoint from providers.forecast_url, coordinates from env")
