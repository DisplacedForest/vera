"""Projection of code-backed flows into served workflow definitions. Standalone —
run: pytest tests/test_workflow_projection.py

Covers the universal-workflow contract: every scheduler job serves a definition
with a Schedule trigger at the head, scheduled flows project a single named step,
veins project their block pipelines, locked profiles reject structural edits but
round-trip the schedule through draft and promote.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from routers import agentic  # noqa: E402
from routers import scheduler  # noqa: E402
from routers import scheduler_store  # noqa: E402
from routers import workflow_projection  # noqa: E402
from routers import workflow_registry  # noqa: E402
from routers import workflow_store  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler_store, "DB_PATH", str(tmp_path / "scheduler.db"))
    monkeypatch.setattr(workflow_store, "DB_PATH", str(tmp_path / "workflows.db"))
    yield


def test_scheduled_flow_projects_trigger_and_single_step():
    definition = workflow_projection.definition_for("home_model")
    assert definition["id"] == "home_model"
    assert definition["label"] == "Home model"
    nodes = definition["nodes"]
    assert [node["id"] for node in nodes] == ["schedule", "run"]
    assert nodes[0]["type"] == "trigger.schedule"
    assert nodes[0]["config"] == {"mode": "daily", "time": "03:30"}
    assert nodes[1]["type"] == "step.home_model"
    assert definition["edges"] == [{"from": "schedule", "to": "run"}]


def test_weekly_flow_projects_weekly_schedule():
    definition = workflow_projection.definition_for("weight_fit")
    assert definition["nodes"][0]["config"] == {"mode": "weekly", "weekday": "sunday", "time": "04:30"}


def test_pulse_is_not_projected():
    assert workflow_projection.definition_for("pulse") is None
    assert workflow_projection.is_projected("pulse") is False
    assert workflow_projection.is_projected("home_model") is True
    assert workflow_projection.is_projected("nope") is False


def test_vein_flow_projects_its_block_pipeline(vein_shapes):
    definition = workflow_projection.definition_for("vein_weather")
    assert definition["label"] == "Weather"
    nodes = definition["nodes"]
    assert [node["type"] for node in nodes] == ["trigger.schedule", "http_fetch", "trip_band"]
    assert nodes[0]["config"] == {"mode": "interval", "every_minutes": 360}
    assert definition["edges"] == [{"from": "schedule", "to": "step-1"},
                                   {"from": "step-1", "to": "step-2"}]


def test_step_specs_join_the_catalog_with_descriptions():
    entries = {entry["type"]: entry for entry in workflow_registry.catalog()}
    for job_id in scheduler.REGISTRY:
        if job_id == "pulse":
            continue
        entry = entries[f"step.{job_id}"]
        assert entry["description"].strip()
        assert entry["insertable"] is False


def test_profiles_carry_the_locked_flag(vein_shapes):
    assert workflow_registry.profile_for("pulse")["locked"] is False
    assert workflow_registry.profile_for("home_model")["locked"] is True
    assert workflow_registry.profile_for("vein_weather")["locked"] is True
    assert workflow_registry.profile_for("unknown")["locked"] is False


def test_active_falls_back_to_the_projection():
    version = workflow_store.active("home_model")
    assert version["workflow_id"] == "home_model"
    assert version["state"] == "active"
    assert version["version"] == 0
    assert [node["id"] for node in version["definition"]["nodes"]] == ["schedule", "run"]


def test_unknown_workflow_still_raises():
    with pytest.raises(KeyError):
        workflow_store.active("nope")


def test_locked_draft_rejects_structural_edits():
    draft = workflow_store.create_draft("home_model")
    definition = draft["definition"]
    definition["nodes"].append({"id": "extra", "type": "flow.notify"})
    definition["edges"].append({"from": "run", "to": "extra"})
    with pytest.raises(ValueError, match="managed by the server"):
        workflow_store.save_draft(draft["id"], definition)


def test_locked_draft_rejects_edge_changes():
    draft = workflow_store.create_draft("home_model")
    definition = draft["definition"]
    definition["edges"] = []
    with pytest.raises(ValueError, match="managed by the server"):
        workflow_store.save_draft(draft["id"], definition)


def test_locked_schedule_round_trips_through_draft_and_promote():
    draft = workflow_store.create_draft("home_model")
    definition = draft["definition"]
    for node in definition["nodes"]:
        if node["type"] == "trigger.schedule":
            node["config"] = {"mode": "daily", "time": "06:15"}
    workflow_store.save_draft(draft["id"], definition)
    workflow_store.promote(draft["id"])
    assert scheduler.job_cron("home_model") == "15 6 * * *"
    active = workflow_store.active("home_model")
    trigger = next(node for node in active["definition"]["nodes"]
                   if node["type"] == "trigger.schedule")
    assert trigger["config"] == {"mode": "daily", "time": "06:15"}
    assert [node["id"] for node in active["definition"]["nodes"]] == ["schedule", "run"]


def test_locked_active_structure_stays_projected_after_promote(monkeypatch):
    draft = workflow_store.create_draft("home_model")
    workflow_store.save_draft(draft["id"], draft["definition"])
    workflow_store.promote(draft["id"])
    original = workflow_projection.definition_for

    def reshaped(workflow_id):
        projection = original(workflow_id)
        if workflow_id == "home_model" and projection:
            projection["nodes"][1] = {"id": "run", "type": "step.home_model", "config": {}}
            projection["nodes"].append({"id": "audit", "type": "step.knowledge_groom"})
            projection["edges"].append({"from": "run", "to": "audit"})
        return projection

    monkeypatch.setattr(workflow_projection, "definition_for", reshaped)
    active = workflow_store.active("home_model")
    assert [node["id"] for node in active["definition"]["nodes"]] == ["schedule", "run", "audit"]


def test_vein_schedule_round_trips(vein_shapes):
    draft = workflow_store.create_draft("vein_weather")
    definition = draft["definition"]
    for node in definition["nodes"]:
        if node["type"] == "trigger.schedule":
            node["config"] = {"mode": "interval", "every_minutes": 120}
    workflow_store.save_draft(draft["id"], definition)
    workflow_store.promote(draft["id"])
    assert scheduler.job_cron("vein_weather") == "0 */2 * * *"


def test_workflow_endpoint_serves_the_flow_face():
    import asyncio
    out = asyncio.run(agentic.workflow("home_model"))
    assert out["flow"]["label"] == "Home model"
    assert out["flow"]["description"].strip()
    assert out["trigger_job"]["id"] == "home_model"
    catalog = asyncio.run(agentic.workflow_catalog("home_model"))
    assert catalog["profile"]["locked"] is True


def test_vein_steps_carry_stock_block_params(vein_shapes):
    definition = workflow_projection.definition_for("vein_weather")
    steps = definition["nodes"][1:]
    assert steps[0]["config"] == {"url": "https://forecast.example/v1.json", "extract": "gust"}
    assert steps[1]["config"] == {"hi": 45}


def test_step_config_coerces_and_filters():
    schema = workflow_registry.block_config_schema("present")
    config = workflow_projection._step_config(
        {"stats": [{"value": "{data.temp}", "label": "Now"}], "unknown": 1}, schema)
    assert config == {"stats": '[{"value": "{data.temp}", "label": "Now"}]'}
    schema = workflow_registry.block_config_schema("trip_band")
    config = workflow_projection._step_config(
        {"hi": 45, "lo": "not a number", "severity": "bogus", "field": "value"}, schema)
    assert config == {"hi": 45, "field": "value"}


def test_stock_blocks_declare_schemas_in_the_catalog():
    entries = {entry["type"]: entry for entry in workflow_registry.catalog()}
    for block in ("web_search", "http_fetch", "ha_state", "trip_band",
                  "llm_judge", "llm_compose", "situation_cluster", "present"):
        assert entries[block]["description"].strip()
        assert entries[block]["config_schema"]


def test_unrepresentable_cron_serves_honest_trigger(monkeypatch):
    monkeypatch.setenv("SCHEDULE_HOME_MODEL", "13 4 */2 * *")
    definition = workflow_projection.definition_for("home_model")
    trigger = definition["nodes"][0]
    assert trigger["config"] == {}
    assert trigger["rule"] == "13 4 */2 * *"
    active = workflow_store.active("home_model")
    served = active["definition"]["nodes"][0]
    assert served["config"] == {}
    assert served["rule"] == "13 4 */2 * *"


def test_unrepresentable_cron_promote_never_rewrites_the_job(monkeypatch):
    monkeypatch.setenv("SCHEDULE_HOME_MODEL", "13 4 */2 * *")
    draft = workflow_store.create_draft("home_model")
    workflow_store.save_draft(draft["id"], draft["definition"])
    workflow_store.promote(draft["id"])
    assert scheduler.job_cron("home_model") == "13 4 */2 * *"
    served = workflow_store.active("home_model")["definition"]["nodes"][0]
    assert served["config"] == {}
    assert served["rule"] == "13 4 */2 * *"


def test_locked_draft_rejects_a_retyped_node():
    draft = workflow_store.create_draft("home_model")
    definition = draft["definition"]
    for node in definition["nodes"]:
        if node["id"] == "run":
            node["type"] = "step.knowledge_groom"
    with pytest.raises(ValueError, match="managed by the server"):
        workflow_store.save_draft(draft["id"], definition)


def test_duplicate_edges_are_rejected():
    definition = {
        "nodes": [{"id": "a", "type": "flow.filter"}, {"id": "b", "type": "flow.notify"}],
        "edges": [{"from": "a", "to": "b"}, {"from": "a", "to": "b"}],
    }
    with pytest.raises(ValueError, match="more than once"):
        workflow_registry.validate_definition("custom", definition)


def test_deleted_vein_reads_gone(vein_shapes):
    draft = workflow_store.create_draft("vein_weather")
    workflow_store.save_draft(draft["id"], draft["definition"])
    workflow_store.promote(draft["id"])
    (vein_shapes / "weather.json").unlink()
    with pytest.raises(KeyError):
        workflow_store.active("vein_weather")
    workflow_store.drop_workflow("vein_weather")
    assert workflow_store._version_row("vein_weather") is None
