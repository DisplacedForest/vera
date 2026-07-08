"""Vein definition origins — shipped files, the custom directory, merge rules, and
the definition CRUD surface. Run under pytest."""
import asyncio
import json
import os

import pytest
from fastapi import HTTPException

from routers import pulse_veins, vein_defs, vein_store


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(vein_store, "PATH", str(tmp_path / "veins.json"))
    monkeypatch.setattr(vein_defs, "CUSTOM_DIR", str(tmp_path / "veins.d"))
    yield


def _watcher(kind="geopolitics", **over):
    d = {
        "kind": kind,
        "label": "Geopolitics",
        "icon": "globe",
        "blurb": "watches the global geopolitical climate",
        "pipeline": [
            {"block": "web_search", "params": {"query": "geopolitical escalation"}},
            {"block": "llm_judge", "params": {"bar": "would plausibly affect the household"}},
            {"block": "llm_compose"},
        ],
        "schedule": "0 */6 * * *",
    }
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


# --------------------------------------------------------------------------- shipped origin

def test_tree_ships_no_veins(monkeypatch):
    monkeypatch.setattr(vein_defs, "_shipped_cache", None)
    assert vein_defs.shipped() == []


def test_shipped_matches_catalog_module():
    assert pulse_veins.VEINS == vein_defs.shipped()


def test_shipped_skips_pipeline_draft_siblings(monkeypatch, tmp_path):
    shipped_dir = tmp_path / "veins"
    shipped_dir.mkdir()
    (shipped_dir / "status.json").write_text(json.dumps({
        "kind": "status", "label": "System", "icon": "gearshape",
        "producer_jobs": ["updates"]}))
    (shipped_dir / "status.pipeline.json").write_text("{not even json")
    monkeypatch.setattr(vein_defs, "SHIPPED_DIR", str(shipped_dir))
    monkeypatch.setattr(vein_defs, "_shipped_cache", None)
    assert [d["kind"] for d in vein_defs.shipped()] == ["status"]


# --------------------------------------------------------------------------- custom origin

def test_save_custom_round_trips():
    saved = vein_defs.save_custom(_watcher())
    assert vein_defs.customs()["geopolitics"]["label"] == "Geopolitics"
    assert saved["order"] >= 1


def test_save_custom_rejects_shipped_kind(monkeypatch, tmp_path):
    _ship(monkeypatch, tmp_path, _watcher(kind="tanks"))
    with pytest.raises(ValueError):
        vein_defs.save_custom(_watcher(kind="tanks"))


def test_corrupt_custom_file_skipped_not_fatal(tmp_path):
    vein_defs.save_custom(_watcher())
    os.makedirs(vein_defs.CUSTOM_DIR, exist_ok=True)
    with open(os.path.join(vein_defs.CUSTOM_DIR, "broken.json"), "w", encoding="utf-8") as f:
        f.write("{not json")
    assert set(vein_defs.customs()) == {"geopolitics"}
    report = vein_defs.load_report()
    assert len(report) == 1 and "broken.json" in report[0]["file"]


def test_shipped_colliding_custom_file_skipped(monkeypatch, tmp_path):
    _ship(monkeypatch, tmp_path, _watcher(kind="tanks"))
    os.makedirs(vein_defs.CUSTOM_DIR, exist_ok=True)
    with open(os.path.join(vein_defs.CUSTOM_DIR, "tanks.json"), "w", encoding="utf-8") as f:
        json.dump(_watcher(kind="tanks"), f)
    assert vein_defs.customs() == {}
    assert vein_defs.load_report()


def test_delete_custom():
    vein_defs.save_custom(_watcher())
    assert vein_defs.delete_custom("geopolitics") is True
    assert vein_defs.customs() == {}
    assert vein_defs.delete_custom("geopolitics") is False


# --------------------------------------------------------------------------- merged catalog

def _catalog():
    return asyncio.run(pulse_veins.catalog())


def test_custom_vein_appears_in_catalog_with_engine_met():
    vein_defs.save_custom(_watcher())
    entry = next(l for l in _catalog()["veins"] if l["kind"] == "geopolitics")
    assert entry["origin"] == "custom"
    assert entry["can_enable"] is True
    assert any(r["kind"] == "engine" and r["met"] for r in entry["requires"])
    assert [s["block"] for s in entry["pipeline"]] == ["web_search", "llm_judge", "llm_compose"]
    assert entry["schedule"] == "0 */6 * * *"


def test_shipped_entries_carry_origin(monkeypatch, tmp_path):
    _ship(monkeypatch, tmp_path, _watcher(kind="tanks"))
    entry = next(l for l in _catalog()["veins"] if l["kind"] == "tanks")
    assert entry["origin"] == "shipped"


def test_pipeline_vein_enables_through_the_engine_requirement():
    vein_defs.save_custom(_watcher())
    asyncio.run(pulse_veins.update_vein("geopolitics", pulse_veins.VeinUpdate(enabled=True)))
    assert pulse_veins.is_enabled("geopolitics")


def test_custom_vein_survives_rescan():
    vein_defs.save_custom(_watcher())
    assert "geopolitics" in {l["kind"] for l in _catalog()["veins"]}
    assert "geopolitics" in {l["kind"] for l in _catalog()["veins"]}


# --------------------------------------------------------------------------- definition CRUD

def test_post_definition_creates():
    entry = asyncio.run(pulse_veins.create_vein(_watcher()))
    assert entry["kind"] == "geopolitics" and entry["origin"] == "custom"


def test_post_collision_409(monkeypatch, tmp_path):
    _ship(monkeypatch, tmp_path, _watcher(kind="tanks"))
    asyncio.run(pulse_veins.create_vein(_watcher()))
    for kind in ("geopolitics", "tanks"):
        with pytest.raises(HTTPException) as e:
            asyncio.run(pulse_veins.create_vein(_watcher(kind=kind)))
        assert e.value.status_code == 409


def test_post_invalid_definition_422():
    with pytest.raises(HTTPException) as e:
        asyncio.run(pulse_veins.create_vein(_watcher(schedule="whenever")))
    assert e.value.status_code == 422


def test_put_definition_edits_custom_only(monkeypatch, tmp_path):
    _ship(monkeypatch, tmp_path, _watcher(kind="tanks"))
    asyncio.run(pulse_veins.create_vein(_watcher()))
    entry = asyncio.run(pulse_veins.replace_definition(
        "geopolitics", _watcher(blurb="sharper bar")))
    assert entry["blurb"] == "sharper bar"
    with pytest.raises(HTTPException) as e:
        asyncio.run(pulse_veins.replace_definition("tanks", _watcher(kind="tanks")))
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        asyncio.run(pulse_veins.replace_definition("nope", _watcher(kind="nope")))
    assert e.value.status_code == 404


def test_put_definition_kind_follows_path():
    asyncio.run(pulse_veins.create_vein(_watcher()))
    with pytest.raises(HTTPException) as e:
        asyncio.run(pulse_veins.replace_definition("geopolitics", _watcher(kind="renamed")))
    assert e.value.status_code == 422


def test_delete_definition_clears_state(monkeypatch, tmp_path):
    _ship(monkeypatch, tmp_path, _watcher(kind="tanks"))
    asyncio.run(pulse_veins.create_vein(_watcher()))
    vein_store.update("geopolitics", options={"noop": True})
    asyncio.run(pulse_veins.delete_vein("geopolitics"))
    assert "geopolitics" not in {l["kind"] for l in _catalog()["veins"]}
    assert "geopolitics" not in vein_store.load()
    for kind, code in (("tanks", 403), ("nope", 404)):
        with pytest.raises(HTTPException) as e:
            asyncio.run(pulse_veins.delete_vein(kind))
        assert e.value.status_code == code


def test_schema_endpoint():
    schema = asyncio.run(pulse_veins.definition_schema())
    assert schema["properties"]["kind"]["pattern"]
