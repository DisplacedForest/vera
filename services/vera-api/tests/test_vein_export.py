import asyncio
import json

import pytest
from fastapi import HTTPException

from routers import leak_patterns, pulse_veins, vein_defs, vein_schema, vein_store


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(vein_store, "PATH", str(tmp_path / "veins.json"))
    monkeypatch.setattr(vein_defs, "CUSTOM_DIR", str(tmp_path / "veins.d"))
    yield


def _watcher(kind="rivergauge", **over):
    d = {
        "kind": kind,
        "label": "River gauge",
        "icon": "water.waves",
        "blurb": "watches the river level",
        "pipeline": [
            {"block": "http_fetch", "params": {"url": "{providers.gauge_url}", "extract": "level"}},
            {"block": "trip_band", "params": {"hi": 21.5}},
        ],
        "schedule": "*/30 * * * *",
        "providers": [
            {"id": "gauge_url", "label": "Gauge endpoint",
             "default": "https://waterdata.example/gauge.json", "hint": "any JSON gauge feed"},
        ],
        "options": [
            {"group": "Thresholds", "fields": [
                {"id": "flood_stage", "label": "Flood stage", "type": "number", "default": 21.5},
                {"id": "units", "label": "Units", "type": "choice", "choices": ["ft", "m"],
                 "env": "GAUGE_UNITS", "default": "ft"},
            ]},
        ],
    }
    d.update(over)
    return d


def _valid(raw):
    return vein_schema.validate_definition(raw)


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


def _export(kind):
    resp = asyncio.run(pulse_veins.export_vein(kind))
    return resp, json.loads(resp.body)


def test_sanitize_clears_provider_defaults():
    clean = vein_defs._sanitize(_valid(_watcher()))
    assert clean["providers"][0]["default"] == ""


def test_sanitize_clears_env_seeded_option_defaults():
    clean = vein_defs._sanitize(_valid(_watcher()))
    fields = {f["id"]: f for f in clean["options"][0]["fields"]}
    assert fields["units"]["default"] is None
    assert fields["flood_stage"]["default"] == 21.5


@pytest.mark.parametrize("secret", [
    "sk-" + "A" * 24,
    "ey" + "JhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" + "." + "ey" + "JzdWIiOiIxMjM0NTY3ODkwIn0",
    "-----BEGIN RSA " + "PRIVATE KEY-----",
    "d" * 40,
    ("ABCDefgh+/" * 4) + "xy",
])
def test_sanitize_clears_key_shaped_strings(secret):
    assert leak_patterns.looks_secret(secret)
    clean = vein_defs._sanitize(_valid(_watcher(blurb=secret)))
    assert clean["blurb"] == ""


def test_sanitize_keeps_ordinary_prose():
    clean = vein_defs._sanitize(_valid(_watcher(blurb="watches the river level")))
    assert clean["blurb"] == "watches the river level"


def test_export_stamps_format_and_attachment():
    vein_defs.save_custom(_watcher())
    resp, payload = _export("rivergauge")
    assert payload["format"] == vein_defs.FORMAT
    assert resp.headers["content-disposition"] == 'attachment; filename="rivergauge.vein.json"'
    assert payload["providers"][0]["default"] == ""


def test_export_unknown_kind_404():
    with pytest.raises(HTTPException) as e:
        _export("nope")
    assert e.value.status_code == 404


def test_round_trip_reproduces_fields_except_sanitized():
    original = vein_defs.save_custom(_watcher())
    _, payload = _export("rivergauge")
    vein_defs.delete_custom("rivergauge")
    vein_store.remove("rivergauge")

    result = asyncio.run(pulse_veins.import_vein(payload))
    assert result["ok"] and result["kind"] == "rivergauge"
    imported = vein_defs.customs()["rivergauge"]

    for key in ("kind", "label", "icon", "blurb", "pipeline", "schedule"):
        assert imported[key] == original[key]
    assert imported["providers"][0]["default"] == ""
    fields = {f["id"]: f for f in imported["options"][0]["fields"]}
    assert "default" not in fields["units"]
    assert fields["flood_stage"]["default"] == 21.5


def test_import_lands_disabled():
    vein_defs.save_custom(_watcher())
    _, payload = _export("rivergauge")
    vein_defs.delete_custom("rivergauge")
    vein_store.remove("rivergauge")
    asyncio.run(pulse_veins.import_vein(payload))
    assert pulse_veins.is_enabled("rivergauge") is False


def test_journal_field_round_trips():
    vein_defs.save_custom(_watcher(journal=True))
    _, payload = _export("rivergauge")
    assert payload["journal"] is True
    vein_defs.delete_custom("rivergauge")
    vein_store.remove("rivergauge")
    asyncio.run(pulse_veins.import_vein(payload))
    assert vein_defs.customs()["rivergauge"]["journal"] is True


def test_import_shipped_kind_409(monkeypatch, tmp_path):
    _ship(monkeypatch, tmp_path, _watcher(kind="tanks"))
    with pytest.raises(HTTPException) as e:
        asyncio.run(pulse_veins.import_vein({**_watcher(kind="tanks"), "format": vein_defs.FORMAT}))
    assert e.value.status_code == 409


def test_import_custom_collision_409_carries_label():
    vein_defs.save_custom(_watcher(label="River gauge"))
    with pytest.raises(HTTPException) as e:
        asyncio.run(pulse_veins.import_vein({**_watcher(), "format": vein_defs.FORMAT}))
    assert e.value.status_code == 409
    assert "River gauge" in e.value.detail


def test_import_unknown_block_warns_but_succeeds():
    defn = _watcher(pipeline=[{"block": "moon_phase", "params": {}}])
    result = asyncio.run(pulse_veins.import_vein({**defn, "format": vein_defs.FORMAT}))
    assert result["ok"] is True
    blocks = [w for w in result["warnings"] if w["type"] == "block"]
    assert blocks == [{"type": "block", "id": "moon_phase", "label": "moon_phase"}]
    assert "rivergauge" in vein_defs.customs()


def test_import_unmet_requirement_warns():
    defn = _watcher(requires=[{"kind": "integration", "id": "overseerr", "label": "Overseerr"}])
    result = asyncio.run(pulse_veins.import_vein({**defn, "format": vein_defs.FORMAT}))
    assert result["ok"] is True
    reqs = [w for w in result["warnings"] if w["type"] == "requirement"]
    assert reqs and reqs[0]["id"] == "overseerr"


def test_import_newer_format_rejected():
    with pytest.raises(HTTPException) as e:
        asyncio.run(pulse_veins.import_vein({**_watcher(), "format": vein_defs.FORMAT + 1}))
    assert e.value.status_code == 422
    assert "newer" in e.value.detail


def test_import_invalid_definition_422():
    with pytest.raises(HTTPException) as e:
        asyncio.run(pulse_veins.import_vein({**_watcher(schedule="whenever"), "format": vein_defs.FORMAT}))
    assert e.value.status_code == 422
