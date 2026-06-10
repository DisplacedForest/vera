"""Pulse lane catalog/store — opt-in lanes, cap enforcement, gates, option scoping,
and upgrade seeding. Run under pytest."""
import asyncio
import os

import pytest
from fastapi import HTTPException

from routers import lane_store, pulse_lanes, signals
from routers import scheduler as sch


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(lane_store, "PATH", str(tmp_path / "lanes.json"))
    for var in ("WEATHER_LAT", "WEATHER_LON", "HOME_STATE",
                "SIGNALS_ORIENTATION", "SIGNALS_IMPACT_GOODS", "TEMPERATURE_UNIT"):
        monkeypatch.delenv(var, raising=False)
    yield


def _put(kind, **kw):
    return asyncio.run(pulse_lanes.update_lane(kind, pulse_lanes.LaneUpdate(**kw)))


# --------------------------------------------------------------------------- opt-in posture

def test_fresh_install_has_no_lanes():
    assert pulse_lanes.lanes() == []
    assert pulse_lanes.enabled_kinds() == set()
    cat = asyncio.run(pulse_lanes.catalog())
    assert cat["active"] == 0 and len(cat["lanes"]) == len(pulse_lanes.LANES)
    assert all(not l["enabled"] for l in cat["lanes"])


def test_enable_disable_round_trip():
    _put("signals", enabled=True)
    assert pulse_lanes.is_enabled("signals")
    assert [l["kind"] for l in pulse_lanes.lanes()] == ["signals"]
    _put("signals", enabled=False)
    assert pulse_lanes.lanes() == []


def test_unknown_lane_404():
    with pytest.raises(HTTPException) as e:
        _put("nope", enabled=True)
    assert e.value.status_code == 404


# --------------------------------------------------------------------------- requirements + cap

def test_weather_requires_coordinates(monkeypatch):
    with pytest.raises(HTTPException) as e:
        _put("weather", enabled=True)
    assert e.value.status_code == 409 and "WEATHER_LAT" in e.value.detail
    monkeypatch.setenv("WEATHER_LAT", "39.0")
    monkeypatch.setenv("WEATHER_LON", "-95.0")
    assert _put("weather", enabled=True)["enabled"]


def test_media_requires_overseerr_integration():
    with pytest.raises(HTTPException) as e:
        _put("media", enabled=True)
    assert e.value.status_code == 409


def test_cap_enforced(monkeypatch):
    # widen the catalog with stub lanes to exceed the cap (the catalog MAY exceed it)
    stubs = [{"kind": f"stub{i}", "label": f"Stub {i}", "icon": "circle", "order": 10 + i,
              "nominal_label": "quiet", "blurb": "stub", "producer_jobs": [],
              "requires": [], "providers": [], "options": []} for i in range(7)]
    monkeypatch.setattr(pulse_lanes, "LANES", pulse_lanes.LANES + stubs)
    monkeypatch.setattr(pulse_lanes, "_BY_KIND", {l["kind"]: l for l in pulse_lanes.LANES})
    for i in range(6):
        _put(f"stub{i}", enabled=True)
    with pytest.raises(HTTPException) as e:
        _put("stub6", enabled=True)
    assert e.value.status_code == 409 and "cap" in e.value.detail
    # an already-active lane can still be edited past the cap check
    assert _put("stub0", enabled=True)["enabled"]


# --------------------------------------------------------------------------- options + providers

def test_option_resolution_store_over_env_over_default(monkeypatch):
    f = {"id": "orientation"}  # signals text option with env fallback
    assert pulse_lanes.option_values("signals")["orientation"] == ""          # manifest default
    monkeypatch.setenv("SIGNALS_ORIENTATION", "from env")
    assert pulse_lanes.option_values("signals")["orientation"] == "from env"  # env seeds
    _put("signals", options={"orientation": "from store"})
    assert pulse_lanes.option_values("signals")["orientation"] == "from store"  # store wins


def test_unknown_option_rejected():
    with pytest.raises(HTTPException) as e:
        _put("signals", options={"bogus": True})
    assert e.value.status_code == 422


def test_provider_slot_resolution():
    assert pulse_lanes.provider_values("weather")["forecast_url"].startswith("https://api.open-meteo.com")
    _put("weather", providers={"forecast_url": "https://forecast.example/v1"})
    assert pulse_lanes.provider_values("weather")["forecast_url"] == "https://forecast.example/v1"


def test_bool_and_number_coercion():
    _put("status", options={"src_containers": "false"})
    _put("weather", providers=None, options={"gust_threshold": "60"})
    assert pulse_lanes.option_values("status")["src_containers"] is False
    assert pulse_lanes.option_values("weather")["gust_threshold"] == 60.0


# --------------------------------------------------------------------------- gates

def test_scheduler_gates_follow_lane_state(monkeypatch):
    assert "lane is off" in sch._gate_reason("weather")
    assert "lane is off" in sch._gate_reason("signals")
    assert "lane is off" in sch._gate_reason("updates")
    _put("signals", enabled=True)
    assert sch._gate_reason("signals") is None
    monkeypatch.setenv("WEATHER_LAT", "39.0")
    monkeypatch.setenv("WEATHER_LON", "-95.0")
    _put("weather", enabled=True)
    assert sch._gate_reason("weather") is None


def test_disabled_producer_refuses_directly():
    out = asyncio.run(signals.check(signals.SignalsRequest()))
    assert out.get("disabled") and "lane is off" in out["detail"]


# --------------------------------------------------------------------------- signals scoping

def test_lane_sentinels_none_without_stored_choice():
    assert signals.lane_sentinels() is None  # env/auto gating applies


def test_lane_sentinels_select_groups():
    _put("signals", options={"grp_financial": True, "grp_geophysical": False,
                             "grp_civic": False, "grp_grid": False, "grp_news": False})
    allow = signals.lane_sentinels(fred_key="k", eia_ok=True)
    assert allow == {"treasury", "vix", "fred_hy"}
    # key-gated members skip quietly without keys
    assert signals.lane_sentinels(fred_key="", eia_ok=False) == {"treasury", "vix"}


def test_orientation_flows_from_lane_option():
    _put("signals", options={"orientation": "shift the harvest plan"})
    assert signals.effective_orientation() == "shift the harvest plan"
    assert "shift the harvest plan" in signals.news_judge_sys(signals.effective_orientation())


# --------------------------------------------------------------------------- system scoping

def test_updates_scoped_to_monitored_sources():
    from routers.updates import _scope_components
    comps = [{"group": "Containers", "id": "docker:x"},
             {"group": "Home Assistant", "id": "update.core"},
             {"group": "HACS", "id": "update.hacs_thing"},
             {"group": "Network", "id": "update.switch"}]
    only_ha = _scope_components(comps, {"src_containers": False, "src_network": False,
                                        "src_home_assistant": True})
    assert {c["group"] for c in only_ha} == {"Home Assistant", "HACS"}
    assert _scope_components(comps, {}) == comps  # nothing stored -> everything on


# --------------------------------------------------------------------------- seeding

def test_fresh_env_seeds_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(lane_store, "PATH", str(tmp_path / "fresh.json"))
    assert pulse_lanes.enabled_kinds() == set()
    assert lane_store.load().get("_seeded") is True  # the pass ran and chose nothing


def test_configured_fresh_install_still_seeds_nothing(monkeypatch, tmp_path):
    # full env config but no prior-deployment artifacts: lanes stay opt-in
    monkeypatch.setattr(lane_store, "PATH", str(tmp_path / "fresh.json"))
    monkeypatch.setenv("WEATHER_LAT", "39.0")
    monkeypatch.setenv("WEATHER_LON", "-95.0")
    monkeypatch.setenv("HOME_STATE", "KS")
    assert pulse_lanes.enabled_kinds() == set()
    assert lane_store.load().get("_seeded") is True


def test_upgraded_deployment_seeds_its_lanes(monkeypatch, tmp_path):
    monkeypatch.setattr(lane_store, "PATH", str(tmp_path / "upgrade.json"))
    (tmp_path / "pulse.db").touch()  # a data volume that already ran Vera
    monkeypatch.setenv("WEATHER_LAT", "39.0")
    monkeypatch.setenv("WEATHER_LON", "-95.0")
    monkeypatch.setenv("HOME_STATE", "KS")
    assert {"weather", "signals"} <= pulse_lanes.enabled_kinds()
    assert "media" not in pulse_lanes.enabled_kinds()  # no overseerr integration here
