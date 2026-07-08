"""Worldview/taste/region config — the judgment knobs are config, not code.

Covers sentinel gating, temperature-unit threshold semantics, US-region detection,
orthogonal Mealie axes, and the neutral fallbacks. Run under pytest."""
import pytest

from routers import persona, units, weather
from routers.kitchen import _orthogonal_categories


# --------------------------------------------------------------------------- temperature unit

def test_unit_defaults_to_fahrenheit(monkeypatch):
    monkeypatch.delenv("TEMPERATURE_UNIT", raising=False)
    assert units.unit() == "fahrenheit" and units.label() == "F"


def test_unit_celsius(monkeypatch):
    monkeypatch.setenv("TEMPERATURE_UNIT", "celsius")
    assert units.unit() == "celsius" and units.label() == "C"


def test_thresholds_default_per_unit():
    assert weather.resolve_thresholds("fahrenheit", None, None) == (100.0, 15.0)
    assert weather.resolve_thresholds("celsius", None, None) == (38.0, -9.0)


def test_thresholds_explicit_values_pass_through_in_either_unit():
    assert weather.resolve_thresholds("celsius", 35.0, -5.0) == (35.0, -5.0)
    assert weather.resolve_thresholds("fahrenheit", 95.0, 20.0) == (95.0, 20.0)


# --------------------------------------------------------------------------- US-region detection

def test_home_state_marks_us(monkeypatch):
    monkeypatch.setenv("HOME_STATE", "IN")
    assert persona.home_region_is_us()


def test_us_coordinates_mark_us(monkeypatch):
    monkeypatch.delenv("HOME_STATE", raising=False)
    monkeypatch.setenv("WEATHER_LAT", "39.8")
    monkeypatch.setenv("WEATHER_LON", "-98.6")
    assert persona.home_region_is_us()


def test_non_us_coordinates_do_not(monkeypatch):
    monkeypatch.delenv("HOME_STATE", raising=False)
    monkeypatch.setenv("WEATHER_LAT", "52.52")   # Berlin
    monkeypatch.setenv("WEATHER_LON", "13.40")
    assert not persona.home_region_is_us()


def test_unconfigured_location_is_not_us(monkeypatch):
    monkeypatch.delenv("HOME_STATE", raising=False)
    monkeypatch.delenv("WEATHER_LAT", raising=False)
    monkeypatch.delenv("WEATHER_LON", raising=False)
    assert not persona.home_region_is_us()


# --------------------------------------------------------------------------- config fallbacks

def test_orientation_neutral_default(monkeypatch):
    monkeypatch.delenv("WATCH_ORIENTATION", raising=False)
    assert persona.orientation() == "change what a reasonable household should know or do this week"


def test_orientation_from_env(monkeypatch):
    monkeypatch.setenv("WATCH_ORIENTATION", "shift the harvest plan")
    assert persona.orientation() == "shift the harvest plan"
def test_forecast_link_template_and_region(monkeypatch):
    monkeypatch.setattr(weather, "FORECAST_URL_TMPL", "https://example.com/{lat}/{lon}")
    assert weather._forecast_sources(1.5, 2.5)[0]["url"] == "https://example.com/1.5/2.5"
    monkeypatch.setattr(weather, "FORECAST_URL_TMPL", "")
    monkeypatch.setenv("HOME_STATE", "IN")
    assert "forecast.weather.gov" in weather._forecast_sources(1, 2)[0]["url"]
    monkeypatch.delenv("HOME_STATE", raising=False)
    monkeypatch.delenv("WEATHER_LAT", raising=False)
    monkeypatch.delenv("WEATHER_LON", raising=False)
    assert weather._forecast_sources(1, 2) == []


# --------------------------------------------------------------------------- orthogonal axes

def test_orthogonal_categories_default_empty(monkeypatch):
    monkeypatch.delenv("MEALIE_ORTHOGONAL_CATEGORIES", raising=False)
    assert _orthogonal_categories() == {}


def test_orthogonal_categories_parse_names_and_hints(monkeypatch):
    monkeypatch.setenv("MEALIE_ORTHOGONAL_CATEGORIES",
                       "Preserving=shelf-stable preservation (jams, pickles); Baking")
    got = _orthogonal_categories()
    assert got == {"Preserving": "shelf-stable preservation (jams, pickles)", "Baking": ""}
