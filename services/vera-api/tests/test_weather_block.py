import asyncio

import pytest

from routers import vein_engine, weather

CTX = {"kind": "weather", "options": {}, "providers": {}}

DAILY = {
    "time": ["2026-07-09", "2026-07-10", "2026-07-11"],
    "weather_code": [1, 95, 2],
    "temperature_2m_max": [88.4, 91.2, 101.6],
    "temperature_2m_min": [70.1, 71.0, 74.2],
    "precipitation_probability_max": [10, 80, 20],
    "wind_gusts_10m_max": [12.0, 52.3, 18.0],
}


@pytest.fixture(autouse=True)
def _coords(monkeypatch):
    monkeypatch.setattr(weather, "LAT", 30.0)
    monkeypatch.setattr(weather, "LON", -97.0)
    yield


def _run(coro):
    return asyncio.run(coro)


def _stub_forecast(monkeypatch, daily):
    async def fake(provider, lat, lon, days, unit):
        fake.provider = provider
        fake.unit = unit
        return {"daily": daily}
    monkeypatch.setattr(weather, "_fetch_daily", fake)
    return fake


def test_weather_conditions_registered_as_monitor():
    assert "weather_conditions" in vein_engine.BLOCKS
    assert "weather_conditions" in vein_engine.MONITOR_BLOCKS


def test_weather_emits_one_standing_item(monkeypatch):
    _stub_forecast(monkeypatch, DAILY)
    items = _run(weather._block_weather_conditions([], {}, CTX))
    assert len(items) == 1
    it = items[0]
    assert it["key"] == "weather:watch"
    assert it["title"] == "Weather watch · thunderstorm, wind gusts 52 mph"
    assert "2026-07-10: thunderstorm, wind gusts 52 mph" in it["content"]
    assert "2026-07-11: extreme heat 102F" in it["content"]
    assert it["severity"] == "alert"


def test_weather_calm_forecast_emits_nothing(monkeypatch):
    _stub_forecast(monkeypatch, {**DAILY, "weather_code": [1, 2, 3],
                                 "temperature_2m_max": [80.0, 82.0, 85.0],
                                 "wind_gusts_10m_max": [10.0, 12.0, 9.0]})
    assert _run(weather._block_weather_conditions([], {}, CTX)) == []


def test_weather_content_signature_stable_across_drift(monkeypatch):
    _stub_forecast(monkeypatch, DAILY)
    first = _run(weather._block_weather_conditions([], {}, CTX))[0]
    drift = {**DAILY, "temperature_2m_max": [88.1, 91.4, 101.8],
             "wind_gusts_10m_max": [12.2, 52.0, 17.5]}
    _stub_forecast(monkeypatch, drift)
    second = _run(weather._block_weather_conditions([], {}, CTX))[0]
    assert vein_engine._content_sig(first) == vein_engine._content_sig(second)


def test_weather_options_steer_thresholds(monkeypatch):
    _stub_forecast(monkeypatch, {**DAILY, "weather_code": [1, 2, 3]})
    ctx = {"kind": "weather", "options": {"gust_threshold": 15, "unit": "celsius"},
           "providers": {"forecast_url": "https://meteo.internal/v1/forecast"}}
    fake = _stub_forecast(monkeypatch, {**DAILY, "weather_code": [1, 2, 3]})
    items = _run(weather._block_weather_conditions([], {}, ctx))
    assert fake.provider == "https://meteo.internal/v1/forecast"
    assert fake.unit == "celsius"
    flags = " ".join(f for it in items for f in [it["content"]])
    assert "wind gusts 52 mph" in flags and "wind gusts 18 mph" in flags


def test_weather_unconfigured_is_a_block_error(monkeypatch):
    monkeypatch.setattr(weather, "LAT", None)
    with pytest.raises(vein_engine.BlockError, match="WEATHER_LAT"):
        _run(weather._block_weather_conditions([], {}, CTX))
