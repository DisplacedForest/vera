"""Pulse vein catalog/store — opt-in veins, cap enforcement, gates, option scoping,
and upgrade seeding. Run under pytest."""
import asyncio
import os

import pytest
from fastapi import HTTPException

from routers import vein_store, pulse_veins, signals
from routers import scheduler as sch


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(vein_store, "PATH", str(tmp_path / "veins.json"))
    for var in ("WEATHER_LAT", "WEATHER_LON", "HOME_STATE",
                "SIGNALS_ORIENTATION", "SIGNALS_IMPACT_GOODS", "TEMPERATURE_UNIT"):
        monkeypatch.delenv(var, raising=False)
    yield


def _put(kind, **kw):
    return asyncio.run(pulse_veins.update_vein(kind, pulse_veins.VeinUpdate(**kw)))


# --------------------------------------------------------------------------- opt-in posture

def test_fresh_install_has_no_veins():
    assert pulse_veins.veins() == []
    assert pulse_veins.enabled_kinds() == set()
    cat = asyncio.run(pulse_veins.catalog())
    assert cat["active"] == 0 and len(cat["veins"]) == len(pulse_veins.VEINS)
    assert all(not l["enabled"] for l in cat["veins"])


def test_enable_disable_round_trip():
    _put("signals", enabled=True)
    assert pulse_veins.is_enabled("signals")
    assert [l["kind"] for l in pulse_veins.veins()] == ["signals"]
    _put("signals", enabled=False)
    assert pulse_veins.veins() == []


def test_unknown_vein_404():
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
    # widen the catalog with stub veins to exceed the cap (the catalog MAY exceed it)
    stubs = [{"kind": f"stub{i}", "label": f"Stub {i}", "icon": "circle", "order": 10 + i,
              "nominal_label": "quiet", "blurb": "stub", "producer_jobs": [],
              "requires": [], "providers": [], "options": []} for i in range(7)]
    monkeypatch.setattr(pulse_veins, "VEINS", pulse_veins.VEINS + stubs)
    monkeypatch.setattr(pulse_veins, "_BY_KIND", {l["kind"]: l for l in pulse_veins.VEINS})
    for i in range(6):
        _put(f"stub{i}", enabled=True)
    with pytest.raises(HTTPException) as e:
        _put("stub6", enabled=True)
    assert e.value.status_code == 409 and "cap" in e.value.detail
    # an already-active vein can still be edited past the cap check
    assert _put("stub0", enabled=True)["enabled"]


# --------------------------------------------------------------------------- options + providers

def test_option_resolution_store_over_env_over_default(monkeypatch):
    f = {"id": "orientation"}  # signals text option with env fallback
    assert pulse_veins.option_values("signals")["orientation"] == ""          # manifest default
    monkeypatch.setenv("SIGNALS_ORIENTATION", "from env")
    assert pulse_veins.option_values("signals")["orientation"] == "from env"  # env seeds
    _put("signals", options={"orientation": "from store"})
    assert pulse_veins.option_values("signals")["orientation"] == "from store"  # store wins


def test_unknown_option_rejected():
    with pytest.raises(HTTPException) as e:
        _put("signals", options={"bogus": True})
    assert e.value.status_code == 422


def test_provider_slot_resolution():
    assert pulse_veins.provider_values("weather")["forecast_url"].startswith("https://api.open-meteo.com")
    _put("weather", providers={"forecast_url": "https://forecast.example/v1"})
    assert pulse_veins.provider_values("weather")["forecast_url"] == "https://forecast.example/v1"


def test_bool_and_number_coercion():
    _put("status", options={"src_containers": "false"})
    _put("weather", providers=None, options={"gust_threshold": "60"})
    assert pulse_veins.option_values("status")["src_containers"] is False
    assert pulse_veins.option_values("weather")["gust_threshold"] == 60.0


# --------------------------------------------------------------------------- gates

def test_scheduler_gates_follow_vein_state(monkeypatch):
    assert "vein is off" in sch._gate_reason("vein_weather")
    assert "vein is off" in sch._gate_reason("signals")
    assert "vein is off" in sch._gate_reason("vein_status")
    _put("signals", enabled=True)
    assert sch._gate_reason("signals") is None
    monkeypatch.setenv("WEATHER_LAT", "39.0")
    monkeypatch.setenv("WEATHER_LON", "-95.0")
    _put("weather", enabled=True)
    assert sch._gate_reason("vein_weather") is None


def test_disabled_producer_refuses_directly():
    out = asyncio.run(signals.check(signals.SignalsRequest()))
    assert out.get("disabled") and "vein is off" in out["detail"]


# --------------------------------------------------------------------------- signals scoping

def test_vein_sentinels_none_without_stored_choice():
    assert signals.vein_sentinels() is None  # env/auto gating applies


def test_vein_sentinels_select_groups():
    _put("signals", options={"grp_financial": True, "grp_geophysical": False,
                             "grp_civic": False, "grp_grid": False, "grp_news": False})
    allow = signals.vein_sentinels(fred_key="k", eia_ok=True)
    assert allow == {"treasury", "vix", "fred_hy"}
    # key-gated members skip quietly without keys
    assert signals.vein_sentinels(fred_key="", eia_ok=False) == {"treasury", "vix"}


def test_orientation_flows_from_vein_option():
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
    monkeypatch.setattr(vein_store, "PATH", str(tmp_path / "fresh.json"))
    assert pulse_veins.enabled_kinds() == set()
    assert vein_store.load().get("_seeded") is True  # the pass ran and chose nothing


def test_configured_fresh_install_still_seeds_nothing(monkeypatch, tmp_path):
    # full env config but no prior-deployment artifacts: veins stay opt-in
    monkeypatch.setattr(vein_store, "PATH", str(tmp_path / "fresh.json"))
    monkeypatch.setenv("WEATHER_LAT", "39.0")
    monkeypatch.setenv("WEATHER_LON", "-95.0")
    monkeypatch.setenv("HOME_STATE", "KS")
    assert pulse_veins.enabled_kinds() == set()
    assert vein_store.load().get("_seeded") is True


def test_upgraded_deployment_seeds_its_veins(monkeypatch, tmp_path):
    monkeypatch.setattr(vein_store, "PATH", str(tmp_path / "upgrade.json"))
    (tmp_path / "pulse.db").touch()  # a data volume that already ran Vera
    monkeypatch.setenv("WEATHER_LAT", "39.0")
    monkeypatch.setenv("WEATHER_LON", "-95.0")
    monkeypatch.setenv("HOME_STATE", "KS")
    assert {"weather", "signals"} <= pulse_veins.enabled_kinds()
    assert "media" not in pulse_veins.enabled_kinds()  # no overseerr integration here


# --------------------------------------------------------------------------- legacy adoption

def test_legacy_lanes_file_is_adopted_once(monkeypatch, tmp_path):
    # a pre-rename data volume: lanes.json holds the deployment's choices
    monkeypatch.setattr(vein_store, "PATH", str(tmp_path / "veins.json"))
    (tmp_path / "lanes.json").write_text(
        '{"signals": {"enabled": true, "options": {"grp_news": false}}, "_seeded": true}',
        encoding="utf-8")
    assert pulse_veins.is_enabled("signals")
    assert pulse_veins.option_values("signals").get("grp_news") is False
    assert not (tmp_path / "lanes.json").exists()  # adopted, not copied
    assert (tmp_path / "veins.json").exists()


def test_existing_vein_store_wins_over_legacy_file(monkeypatch, tmp_path):
    monkeypatch.setattr(vein_store, "PATH", str(tmp_path / "veins.json"))
    (tmp_path / "veins.json").write_text('{"weather": {"enabled": true}, "_seeded": true}',
                                         encoding="utf-8")
    (tmp_path / "lanes.json").write_text('{"signals": {"enabled": true}, "_seeded": true}',
                                         encoding="utf-8")
    assert pulse_veins.enabled_kinds() == {"weather"}
    assert (tmp_path / "lanes.json").exists()  # untouched when the vein store already exists


# --------------------------------------------------------------------------- pipeline veins

def _pipeline_env(monkeypatch, tmp_path):
    from routers import pulse_store, scheduler_store, vein_defs, vein_engine_store
    monkeypatch.setattr(vein_defs, "CUSTOM_DIR", str(tmp_path / "veins.d"))
    monkeypatch.setattr(scheduler_store, "DB_PATH", str(tmp_path / "scheduler.db"))
    monkeypatch.setattr(pulse_store, "DB_PATH", str(tmp_path / "pulse.db"))
    monkeypatch.setattr(vein_engine_store, "DB_PATH", str(tmp_path / "engine.db"))


def _pipeline_defn(kind="rivergauge"):
    return {
        "kind": kind, "label": "River gauge", "icon": "water.waves",
        "pipeline": [
            {"block": "http_fetch", "params": {"url": "https://g.example/x.json",
                                               "extract": "level"}},
            {"block": "trip_band", "params": {"hi": 21.5}},
        ],
        "schedule": "*/30 * * * *",
    }


def test_pipeline_vein_is_enableable(monkeypatch, tmp_path):
    _pipeline_env(monkeypatch, tmp_path)
    entry = asyncio.run(pulse_veins.create_vein(_pipeline_defn()))
    assert entry["can_enable"] is True
    assert all(r["met"] for r in entry["requires"])
    _put("rivergauge", enabled=True)
    assert pulse_veins.is_enabled("rivergauge")


def test_create_rejects_unknown_block_naming_the_step(monkeypatch, tmp_path):
    _pipeline_env(monkeypatch, tmp_path)
    bad = _pipeline_defn()
    bad["pipeline"][1] = {"block": "teleport"}
    with pytest.raises(HTTPException) as e:
        asyncio.run(pulse_veins.create_vein(bad))
    assert e.value.status_code == 422
    assert "step 1" in e.value.detail and "teleport" in e.value.detail


def test_pipeline_cron_override_maps_to_dynamic_job(monkeypatch, tmp_path):
    from routers import scheduler_store
    _pipeline_env(monkeypatch, tmp_path)
    asyncio.run(pulse_veins.create_vein(_pipeline_defn()))
    _put("rivergauge", cron="0 9 * * *")
    assert scheduler_store.overrides()["vein_rivergauge"]["cron"] == "0 9 * * *"


def test_pipeline_entry_lists_dynamic_job(monkeypatch, tmp_path):
    _pipeline_env(monkeypatch, tmp_path)
    entry = asyncio.run(pulse_veins.create_vein(_pipeline_defn()))
    assert [j["id"] for j in entry["jobs"]] == ["vein_rivergauge"]
    assert entry["jobs"][0]["cron"] == "*/30 * * * *"


def test_run_endpoint_statuses(monkeypatch, tmp_path):
    _pipeline_env(monkeypatch, tmp_path)
    with pytest.raises(HTTPException) as e:
        asyncio.run(pulse_veins.run_vein_now("nope"))
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        asyncio.run(pulse_veins.run_vein_now("signals"))
    assert e.value.status_code == 422
    asyncio.run(pulse_veins.create_vein(_pipeline_defn()))
    with pytest.raises(HTTPException) as e:
        asyncio.run(pulse_veins.run_vein_now("rivergauge"))
    assert e.value.status_code == 409


def test_run_endpoint_dry_run_returns_cards_without_posting(monkeypatch, tmp_path):
    import json as _json
    from routers import pulse_store, vein_engine
    _pipeline_env(monkeypatch, tmp_path)
    asyncio.run(pulse_veins.create_vein(_pipeline_defn()))

    async def fake_get(url):
        return 200, _json.dumps({"level": 25})
    monkeypatch.setattr(vein_engine, "_get", fake_get)
    out = asyncio.run(pulse_veins.run_vein_now("rivergauge", dry_run=True))
    assert out["dry_run"] and out["situations"] == 1
    assert pulse_store.list_cards() == []
    _put("rivergauge", enabled=True)
    posted = asyncio.run(pulse_veins.run_vein_now("rivergauge"))
    assert posted == {"ok": True, "situations": 1, "cards": 1}
    assert len(pulse_store.list_cards()) == 1
