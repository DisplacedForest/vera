"""Vein definition schema — shape validation, production-shape exclusivity, and the
exported JSON Schema contract. Run under pytest."""
import pytest

from routers import vein_schema


def _watcher(**over):
    d = {
        "kind": "geopolitics",
        "label": "Geopolitics",
        "icon": "globe",
        "nominal_label": "quiet",
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


def _monitor(**over):
    d = {
        "kind": "river_gauge",
        "label": "River",
        "icon": "water.waves",
        "pipeline": [
            {"block": "http_fetch", "params": {"url": "https://gauge.example/site.json",
                                               "extract": "$.value"}},
            {"block": "trip_band", "params": {"above": 21.5}},
            {"block": "llm_compose"},
        ],
        "schedule": "*/30 * * * *",
    }
    d.update(over)
    return d


def test_watcher_definition_validates():
    out = vein_schema.validate_definition(_watcher())
    assert out["kind"] == "geopolitics"
    assert [s["block"] for s in out["pipeline"]] == ["web_search", "llm_judge", "llm_compose"]
    assert out["schedule"] == "0 */6 * * *"


def test_monitor_definition_validates():
    out = vein_schema.validate_definition(_monitor())
    assert out["pipeline"][1]["params"] == {"above": 21.5}


def test_producer_jobs_definition_validates():
    out = vein_schema.validate_definition({
        "kind": "weather", "label": "Weather", "icon": "cloud.sun",
        "producer_jobs": ["weather"],
    })
    assert out["producer_jobs"] == ["weather"]
    assert "pipeline" not in out


def test_registered_style_block_name_validates_structurally():
    out = vein_schema.validate_definition(_watcher(pipeline=[{"block": "tide_gauge"},
                                                             {"block": "llm_compose"}]))
    assert out["pipeline"][0]["block"] == "tide_gauge"


def test_block_name_pattern_enforced():
    with pytest.raises(ValueError):
        vein_schema.validate_definition(_watcher(pipeline=[{"block": "Not-A-Block"}]))


def test_both_production_shapes_rejected():
    with pytest.raises(ValueError):
        vein_schema.validate_definition(_watcher(producer_jobs=["weather"]))


def test_neither_production_shape_rejected():
    bad = _watcher()
    del bad["pipeline"]
    del bad["schedule"]
    with pytest.raises(ValueError):
        vein_schema.validate_definition(bad)


def test_pipeline_without_schedule_rejected():
    with pytest.raises(ValueError):
        vein_schema.validate_definition(_watcher(schedule=None))


def test_invalid_cron_rejected():
    with pytest.raises(ValueError):
        vein_schema.validate_definition(_watcher(schedule="whenever"))


def test_schedule_on_producer_jobs_vein_rejected():
    with pytest.raises(ValueError):
        vein_schema.validate_definition({
            "kind": "weather", "label": "Weather", "icon": "cloud.sun",
            "producer_jobs": ["weather"], "schedule": "0 * * * *",
        })


def test_kind_pattern_enforced():
    for kind in ("Geo", "9lives", "with-dash", ""):
        with pytest.raises(ValueError):
            vein_schema.validate_definition(_watcher(kind=kind))


def test_unknown_top_level_key_rejected():
    with pytest.raises(ValueError):
        vein_schema.validate_definition(_watcher(mystery=True))


def test_choice_field_requires_choices():
    opts = [{"group": "Units", "fields": [
        {"id": "unit", "label": "Unit", "type": "choice"}]}]
    with pytest.raises(ValueError):
        vein_schema.validate_definition(_watcher(options=opts))
    opts[0]["fields"][0]["choices"] = ["fahrenheit", "celsius"]
    out = vein_schema.validate_definition(_watcher(options=opts))
    assert out["options"][0]["fields"][0]["choices"] == ["fahrenheit", "celsius"]


def test_requirement_shapes_enforced():
    ok = _watcher(requires=[
        {"kind": "integration", "id": "overseerr"},
        {"kind": "feature", "integration": "home_assistant", "feature": "updates"},
        {"kind": "env", "names": ["WEATHER_LAT", "WEATHER_LON"], "label": "home coordinates"},
    ])
    assert len(vein_schema.validate_definition(ok)["requires"]) == 3
    for req in ({"kind": "integration"}, {"kind": "feature", "integration": "x"},
                {"kind": "env"}):
        with pytest.raises(ValueError):
            vein_schema.validate_definition(_watcher(requires=[req]))


def test_json_schema_exported():
    schema = vein_schema.json_schema()
    assert schema["properties"]["kind"]["pattern"]
    blocks = str(schema)
    for b in vein_schema.BLOCKS:
        assert b in blocks
