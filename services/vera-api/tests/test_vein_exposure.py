import asyncio
import json

import pytest

from routers import integrations, pulse_veins, vein_defs, vein_store


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(vein_store, "PATH", str(tmp_path / "veins.json"))
    monkeypatch.setattr(vein_defs, "CUSTOM_DIR", str(tmp_path / "veins.d"))
    yield


def _pipeline():
    return [
        {"block": "http_fetch", "params": {"url": "https://x.example/y.json", "extract": "n"}},
        {"block": "trip_band", "params": {"hi": 1}},
    ]


def _media(**over):
    d = {"kind": "media", "label": "Media", "icon": "film",
         "blurb": "a weekly worth-adding digest", "order": 3,
         "pipeline": _pipeline(), "schedule": "0 9 * * 0",
         "requires": [{"kind": "integration", "id": "overseerr"}]}
    d.update(over)
    return d


def _weather(**over):
    d = {"kind": "weather", "label": "Weather", "icon": "cloud.sun",
         "blurb": "severe-weather pre-warnings", "order": 1,
         "pipeline": _pipeline(), "schedule": "0 */6 * * *",
         "requires": [{"kind": "env", "names": ["WEATHER_LAT", "WEATHER_LON"],
                       "label": "home coordinates"}]}
    d.update(over)
    return d


def _open(**over):
    d = {"kind": "status", "label": "System", "icon": "gearshape",
         "blurb": "service health", "order": 0, "producer_jobs": ["updates"],
         "requires": []}
    d.update(over)
    return d


def _watcher(kind="geopolitics", **over):
    d = {"kind": kind, "label": "Geopolitics", "icon": "globe",
         "blurb": "watches the global geopolitical climate",
         "pipeline": [{"block": "web_search", "params": {"query": "x"}},
                      {"block": "llm_compose"}],
         "schedule": "0 */6 * * *"}
    d.update(over)
    return d


def _ship(monkeypatch, tmp_path, *defs):
    shipped_dir = tmp_path / "shipped"
    shipped_dir.mkdir(exist_ok=True)
    for d in defs:
        (shipped_dir / (d["kind"] + ".json")).write_text(json.dumps(d), encoding="utf-8")
    monkeypatch.setattr(vein_defs, "SHIPPED_DIR", str(shipped_dir))
    monkeypatch.setattr(vein_defs, "_shipped_cache", None)
    shipped = vein_defs.shipped()
    monkeypatch.setattr(pulse_veins, "VEINS", shipped)
    monkeypatch.setattr(pulse_veins, "_BY_KIND", {l["kind"]: l for l in shipped})


def _overseerr(monkeypatch, present):
    monkeypatch.setattr(integrations, "integration",
                        lambda iid, _p=present: {"url": "http://o"} if (iid == "overseerr" and _p) else None)


def _catalog(all_=False):
    return asyncio.run(pulse_veins.catalog(all_=all_))


def _entry(cat, kind):
    return next(l for l in cat["veins"] if l["kind"] == kind)


def test_exposed_true_when_integration_present(monkeypatch, tmp_path):
    _ship(monkeypatch, tmp_path, _media())
    _overseerr(monkeypatch, present=True)
    entry = _entry(_catalog(all_=True), "media")
    assert entry["exposed"] is True
    assert entry["requires_unmet"] == []


def test_exposed_false_when_integration_absent(monkeypatch, tmp_path):
    _ship(monkeypatch, tmp_path, _media())
    _overseerr(monkeypatch, present=False)
    assert _entry(_catalog(all_=True), "media")["exposed"] is False


def test_default_catalog_filters_unexposed(monkeypatch, tmp_path):
    _ship(monkeypatch, tmp_path, _media(), _open())
    _overseerr(monkeypatch, present=False)
    default_kinds = {l["kind"] for l in _catalog()["veins"]}
    all_kinds = {l["kind"] for l in _catalog(all_=True)["veins"]}
    assert "media" not in default_kinds
    assert "status" in default_kinds
    assert "media" in all_kinds and "status" in all_kinds


def test_custom_vein_always_exposed_despite_unmet_requirement(monkeypatch, tmp_path):
    _overseerr(monkeypatch, present=False)
    vein_defs.save_custom(_watcher(requires=[{"kind": "integration", "id": "overseerr"}]))
    entry = _entry(_catalog(), "geopolitics")
    assert entry["origin"] == "custom"
    assert entry["exposed"] is True
    assert entry["requires_unmet"] == []
    assert any(r["kind"] == "integration" and not r["met"] for r in entry["requires"])


def test_pipeline_vein_exposed_despite_engine_requirement(monkeypatch, tmp_path):
    _ship(monkeypatch, tmp_path, _weather())
    monkeypatch.setenv("WEATHER_LAT", "1")
    monkeypatch.setenv("WEATHER_LON", "2")
    entry = _entry(_catalog(all_=True), "weather")
    assert any(r["kind"] == "engine" for r in entry["requires"])
    assert entry["exposed"] is True


def test_env_requirement_gates_exposure_engine_excluded(monkeypatch, tmp_path):
    _ship(monkeypatch, tmp_path, _weather())
    monkeypatch.delenv("WEATHER_LAT", raising=False)
    monkeypatch.delenv("WEATHER_LON", raising=False)
    entry = _entry(_catalog(all_=True), "weather")
    assert entry["exposed"] is False
    unmet_kinds = {u["kind"] for u in entry["requires_unmet"]}
    assert "env" in unmet_kinds
    assert "engine" not in unmet_kinds


def test_integration_configured_flips_exposed_live(monkeypatch, tmp_path):
    _ship(monkeypatch, tmp_path, _media())
    _overseerr(monkeypatch, present=False)
    assert "media" not in {l["kind"] for l in _catalog()["veins"]}
    _overseerr(monkeypatch, present=True)
    assert "media" in {l["kind"] for l in _catalog()["veins"]}


def test_requires_unmet_carries_kind_id_label(monkeypatch, tmp_path):
    _ship(monkeypatch, tmp_path, _media())
    _overseerr(monkeypatch, present=False)
    unmet = _entry(_catalog(all_=True), "media")["requires_unmet"]
    assert unmet
    assert unmet[0]["kind"] == "integration"
    assert unmet[0]["id"] == "overseerr"
    assert unmet[0]["label"]
