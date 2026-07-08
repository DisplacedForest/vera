"""Pytest bootstrap: point every store at a writable temp dir BEFORE
any module is imported.

Each store binds its DB path at import time from a `*_DB_PATH` / `*_DIR` env var
that defaults to `/data/...` (the container's bind mount). On a host without a
writable `/data`, a store imported transitively during collection (e.g.
`routers.home` -> `routers.rhythm_store`) binds to `/data` before a test's own
line-6 override runs, and `init()` fails with a read-only-filesystem error.

pytest imports conftest.py before collecting test modules, so setting these env
vars here puts a writable default in place before any store binds its path —
making the suite host-independent without touching store logic. `setdefault`
keeps any value already provided by the environment (e.g. CI) authoritative.
"""
import json
import os
import tempfile

import pytest

from data_paths import STORE_PATHS

_ROOT = tempfile.mkdtemp(prefix="vera-api-tests-")

for _var, _rel in STORE_PATHS.items():
    os.environ.setdefault(_var, os.path.join(_ROOT, _rel))


VEIN_SHAPES = [
    {"kind": "status", "label": "Status", "icon": "gearshape", "order": 0,
     "nominal_label": "nominal", "blurb": "service health across monitored sources",
     "pipeline": [
         {"block": "http_fetch", "params": {"url": "https://svc.example/health.json",
                                            "extract": "down"}},
         {"block": "trip_band", "params": {"hi": 0.5}},
     ],
     "schedule": "*/15 * * * *",
     "requires": [], "providers": [],
     "options": [
         {"group": "Monitored sources", "fields": [
             {"id": "src_containers", "label": "Containers", "type": "bool", "default": True},
             {"id": "src_home_assistant", "label": "Home Assistant", "type": "bool",
              "default": True},
             {"id": "src_network", "label": "Network gear", "type": "bool", "default": True},
         ]},
     ]},
    {"kind": "weather", "label": "Weather", "icon": "cloud.sun", "order": 1,
     "nominal_label": "clear", "blurb": "severe-weather pre-warnings",
     "pipeline": [
         {"block": "http_fetch", "params": {"url": "https://forecast.example/v1.json",
                                            "extract": "gust"}},
         {"block": "trip_band", "params": {"hi": 45}},
     ],
     "schedule": "0 */6 * * *",
     "requires": [
         {"kind": "env", "names": ["WEATHER_LAT", "WEATHER_LON"], "label": "home coordinates"},
     ],
     "providers": [
         {"id": "forecast_url", "label": "Forecast endpoint",
          "default": "https://api.open-meteo.com/v1/forecast",
          "hint": "any Open-Meteo-compatible forecast API"},
     ],
     "options": [
         {"group": "Units and thresholds", "fields": [
             {"id": "unit", "label": "Temperature unit", "type": "choice",
              "choices": ["fahrenheit", "celsius"], "env": "TEMPERATURE_UNIT",
              "default": "fahrenheit"},
             {"id": "gust_threshold", "label": "Wind gust alert", "type": "number",
              "default": 45},
         ]},
     ]},
    {"kind": "newsdesk", "label": "News desk", "icon": "antenna.radiowaves.left.and.right",
     "order": 2, "nominal_label": "quiet", "blurb": "external-watch threshold monitor",
     "producer_jobs": ["newsdesk"],
     "requires": [], "providers": [],
     "options": [
         {"group": "Sources", "fields": [
             {"id": "src_wire", "label": "Wire feeds", "type": "bool", "default": True},
             {"id": "src_markets", "label": "Markets", "type": "bool", "default": True},
             {"id": "src_local", "label": "Local", "type": "bool", "default": False},
         ]},
         {"group": "Orientation", "fields": [
             {"id": "orientation", "label": "What clears the bar", "type": "text",
              "env": "WATCH_ORIENTATION", "default": ""},
             {"id": "digest", "label": "Digest mode", "type": "bool",
              "env": "WATCH_DIGEST", "default": False},
         ]},
     ]},
    {"kind": "media", "label": "Media", "icon": "film", "order": 3,
     "nominal_label": "quiet", "blurb": "a weekly worth-adding digest",
     "pipeline": [
         {"block": "http_fetch", "params": {"url": "https://library.example/new.json",
                                            "extract": "count"}},
         {"block": "trip_band", "params": {"hi": 1}},
     ],
     "schedule": "0 9 * * 0",
     "requires": [
         {"kind": "feature", "integration": "overseerr", "feature": "media_curation"},
     ],
     "providers": [],
     "options": [
         {"group": "Curation", "fields": [
             {"id": "cap", "label": "Picks per digest", "type": "number",
              "env": "MEDIA_CURATION_CAP", "default": 8},
         ]},
     ]},
]


@pytest.fixture
def vein_shapes(monkeypatch, tmp_path):
    from routers import vein_defs
    d = tmp_path / "veins.d"
    d.mkdir(parents=True, exist_ok=True)
    for shape in VEIN_SHAPES:
        (d / (shape["kind"] + ".json")).write_text(json.dumps(shape), encoding="utf-8")
    monkeypatch.setattr(vein_defs, "CUSTOM_DIR", str(d))
    return d
