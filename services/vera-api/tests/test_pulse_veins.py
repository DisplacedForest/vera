"""Pulse vein catalog/store — opt-in veins, cap enforcement, gates, option scoping.
Run under pytest."""
import asyncio
import os

import pytest
from fastapi import HTTPException

from routers import vein_store, pulse_veins
from routers import scheduler as sch


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path, vein_shapes):
    monkeypatch.setattr(vein_store, "PATH", str(tmp_path / "veins.json"))
    for var in ("WEATHER_LAT", "WEATHER_LON", "HOME_STATE",
                "WATCH_ORIENTATION", "WATCH_DIGEST", "TEMPERATURE_UNIT"):
        monkeypatch.delenv(var, raising=False)
    yield


def _put(kind, **kw):
    return asyncio.run(pulse_veins.update_vein(kind, pulse_veins.VeinUpdate(**kw)))


# --------------------------------------------------------------------------- opt-in posture

def test_fresh_install_has_no_veins():
    assert pulse_veins.veins() == []
    assert pulse_veins.enabled_kinds() == set()
    cat = asyncio.run(pulse_veins.catalog())
    assert cat["active"] == 0
    assert {l["kind"] for l in cat["veins"]} == {"status", "weather", "newsdesk", "media"}
    assert all(not l["enabled"] for l in cat["veins"])


def test_empty_deployment_has_empty_catalog(monkeypatch, tmp_path):
    from routers import vein_defs
    monkeypatch.setattr(vein_defs, "CUSTOM_DIR", str(tmp_path / "none.d"))
    assert pulse_veins.VEINS == []
    cat = asyncio.run(pulse_veins.catalog())
    assert cat["veins"] == [] and cat["active"] == 0
    assert pulse_veins.veins() == []


def test_enable_disable_round_trip():
    _put("newsdesk", enabled=True)
    assert pulse_veins.is_enabled("newsdesk")
    assert [l["kind"] for l in pulse_veins.veins()] == ["newsdesk"]
    _put("newsdesk", enabled=False)
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
    assert pulse_veins.option_values("newsdesk")["orientation"] == ""
    monkeypatch.setenv("WATCH_ORIENTATION", "from env")
    assert pulse_veins.option_values("newsdesk")["orientation"] == "from env"
    _put("newsdesk", options={"orientation": "from store"})
    assert pulse_veins.option_values("newsdesk")["orientation"] == "from store"


def test_unknown_option_rejected():
    with pytest.raises(HTTPException) as e:
        _put("newsdesk", options={"bogus": True})
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
    assert "vein is off" in sch._gate_reason("vein_status")
    _put("status", enabled=True)
    assert sch._gate_reason("vein_status") is None
    monkeypatch.setenv("WEATHER_LAT", "39.0")
    monkeypatch.setenv("WEATHER_LON", "-95.0")
    _put("weather", enabled=True)
    assert sch._gate_reason("vein_weather") is None


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


def test_fresh_env_seeds_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(vein_store, "PATH", str(tmp_path / "fresh.json"))
    assert pulse_veins.enabled_kinds() == set()
    assert vein_store.load() == {}


def test_configured_env_seeds_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(vein_store, "PATH", str(tmp_path / "fresh.json"))
    monkeypatch.setenv("WEATHER_LAT", "39.0")
    monkeypatch.setenv("WEATHER_LON", "-95.0")
    monkeypatch.setenv("HOME_STATE", "KS")
    assert pulse_veins.enabled_kinds() == set()
    assert vein_store.load() == {}


def test_prior_deployment_artifacts_seed_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(vein_store, "PATH", str(tmp_path / "upgrade.json"))
    (tmp_path / "pulse.db").touch()
    monkeypatch.setenv("WEATHER_LAT", "39.0")
    monkeypatch.setenv("WEATHER_LON", "-95.0")
    monkeypatch.setenv("HOME_STATE", "KS")
    assert pulse_veins.enabled_kinds() == set()
    assert vein_store.load() == {}


# --------------------------------------------------------------------------- legacy adoption

def test_legacy_lanes_file_is_adopted_once(monkeypatch, tmp_path):
    # a pre-rename data volume: lanes.json holds the deployment's choices
    monkeypatch.setattr(vein_store, "PATH", str(tmp_path / "veins.json"))
    (tmp_path / "lanes.json").write_text(
        '{"newsdesk": {"enabled": true, "options": {"src_local": false}}}',
        encoding="utf-8")
    assert pulse_veins.is_enabled("newsdesk")
    assert pulse_veins.option_values("newsdesk").get("src_local") is False
    assert not (tmp_path / "lanes.json").exists()  # adopted, not copied
    assert (tmp_path / "veins.json").exists()


def test_existing_vein_store_wins_over_legacy_file(monkeypatch, tmp_path):
    monkeypatch.setattr(vein_store, "PATH", str(tmp_path / "veins.json"))
    (tmp_path / "veins.json").write_text('{"weather": {"enabled": true}}',
                                         encoding="utf-8")
    (tmp_path / "lanes.json").write_text('{"newsdesk": {"enabled": true}}',
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
        asyncio.run(pulse_veins.run_vein_now("newsdesk"))
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
