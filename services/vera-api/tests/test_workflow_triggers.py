import asyncio
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from routers import scheduler
from routers import scheduler_store
from routers import workflow_executor
from routers import workflow_registry
from routers import workflow_store
from routers import workflow_triggers


@pytest.fixture(autouse=True)
def isolated_stores(monkeypatch, tmp_path):
    monkeypatch.setattr(workflow_store, "DB_PATH", str(tmp_path / "workflows.db"))
    monkeypatch.setattr(scheduler_store, "DB_PATH", str(tmp_path / "scheduler.db"))


def _definition():
    return workflow_store.active("pulse")["definition"]


def _trigger(definition):
    return next(node for node in definition["nodes"] if node["type"] == "trigger.schedule")


def test_catalog_serves_schedule_trigger_first_with_plain_schema():
    entries = workflow_registry.catalog()
    assert entries[0]["type"] == "trigger.schedule"
    entry = entries[0]
    assert entry["category"] == "trigger"
    assert entry["label"] == "Schedule"
    assert entry["description"].strip()
    schema = entry["config_schema"]
    assert schema["mode"]["options"] == ["daily", "weekly", "interval"]
    assert "cron" not in json.dumps(schema)


def test_pulse_profile_declares_its_trigger():
    profile = workflow_registry.profile_for("pulse")
    assert profile["triggers"] == ["trigger.schedule"]
    assert workflow_registry.profile_for("anything-else")["triggers"] == []


@pytest.mark.parametrize("config,cron", [
    ({"mode": "daily", "time": "05:00"}, "0 5 * * *"),
    ({"mode": "daily", "time": "23:45"}, "45 23 * * *"),
    ({"mode": "weekly", "weekday": "monday", "time": "09:30"}, "30 9 * * 1"),
    ({"mode": "weekly", "weekday": "sunday", "time": "07:15"}, "15 7 * * 0"),
    ({"mode": "interval", "every_minutes": 15}, "*/15 * * * *"),
    ({"mode": "interval", "every_minutes": 120}, "0 */2 * * *"),
])
def test_schedule_config_maps_to_cron_and_back(config, cron):
    assert workflow_triggers.cron_for(config) == cron
    round_tripped = workflow_triggers.config_for(cron)
    assert workflow_triggers.cron_for(round_tripped) == cron


@pytest.mark.parametrize("config", [
    {"mode": "daily", "time": "25:00"},
    {"mode": "daily", "time": "5am"},
    {"mode": "weekly", "weekday": "someday", "time": "05:00"},
    {"mode": "interval", "every_minutes": 7},
])
def test_bad_schedule_config_is_rejected(config):
    with pytest.raises(ValueError):
        workflow_triggers.cron_for(config)


def test_unrepresentable_cron_is_never_approximated():
    assert workflow_triggers.config_for("30 6 * * 1-5") is None
    assert workflow_triggers.config_for("15 7 1 * *") is None
    assert workflow_triggers.config_for("0 5 * JAN *") is None
    assert workflow_triggers.config_for("*/7 * * * *") is None
    assert workflow_triggers.config_for("bad cron") is None


def test_exotic_pinned_schedule_survives_an_unrelated_promotion(monkeypatch):
    monkeypatch.setenv("SCHEDULE_PULSE", "30 6 * * 1-5")
    assert _trigger(_definition())["config"] == {"mode": "daily", "time": "05:00"}
    draft = workflow_store.create_draft("pulse")
    definition = draft["definition"]
    cover = next(node for node in definition["nodes"] if node["type"] == "pulse.cover_art")
    cover["config"] = {"style": "editorial"}
    workflow_store.save_draft(draft["id"], definition)
    promoted = workflow_store.promote(draft["id"])
    assert promoted["state"] == "active"
    assert scheduler.job_cron("pulse") == "30 6 * * 1-5"


def test_promotion_applies_the_schedule_the_canvas_declares():
    scheduler_store.set_override("pulse", cron="0 6 * * *")
    draft = workflow_store.create_draft("pulse")
    definition = draft["definition"]
    assert _trigger(definition)["config"] == {"mode": "daily", "time": "06:00"}
    _trigger(definition)["config"] = {"mode": "daily", "time": "05:00"}
    workflow_store.save_draft(draft["id"], definition)
    workflow_store.promote(draft["id"])
    assert scheduler.job_cron("pulse") == "0 5 * * *"


def test_promotion_reasserts_a_stale_canvas_schedule():
    draft = workflow_store.create_draft("pulse")
    scheduler_store.set_override("pulse", cron="0 6 * * *")
    workflow_store.promote(draft["id"])
    assert scheduler.job_cron("pulse") == "0 5 * * *"


def test_failed_schedule_write_rolls_back_the_promotion(monkeypatch):
    draft = workflow_store.create_draft("pulse")
    definition = draft["definition"]
    _trigger(definition)["config"] = {"mode": "daily", "time": "09:00"}
    workflow_store.save_draft(draft["id"], definition)

    def boom(job_id, cron):
        raise RuntimeError("db locked")

    monkeypatch.setattr(scheduler, "set_job_cron", boom)
    with pytest.raises(ValueError, match="rolled back"):
        workflow_store.promote(draft["id"])
    assert workflow_store.active("pulse")["version"] == 1
    assert workflow_store.get_version(draft["id"])["state"] == "draft"
    assert scheduler.job_cron("pulse") == "0 5 * * *"


def test_migration_waits_for_a_readable_scheduler(tmp_path):
    legacy = {
        "id": "pulse",
        "nodes": [{"id": "triage", "type": "pulse.triage"},
                  {"id": "gates", "type": "pulse.gates"},
                  {"id": "synthesis", "type": "pulse.synthesis"},
                  {"id": "claim_audit", "type": "pulse.claim_audit"},
                  {"id": "cover_art", "type": "pulse.cover_art"},
                  {"id": "inject", "type": "pulse.inject"}],
        "edges": [{"from": "triage", "to": "gates"}, {"from": "gates", "to": "synthesis"},
                  {"from": "synthesis", "to": "claim_audit"}, {"from": "claim_audit", "to": "cover_art"},
                  {"from": "cover_art", "to": "inject"}],
    }
    path = str(tmp_path / "waiting.db")
    workflow_store.DB_PATH = path
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE workflow_versions (
        id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, version INTEGER NOT NULL,
        state TEXT NOT NULL, definition TEXT NOT NULL, created_at INTEGER NOT NULL,
        promoted_at INTEGER, UNIQUE(workflow_id, version))""")
    conn.execute("INSERT INTO workflow_versions VALUES('v1','pulse',1,'active',?,0,0)",
                 (json.dumps(legacy),))
    conn.commit()
    conn.close()
    with pytest.MonkeyPatch.context() as offline:
        offline.setattr(workflow_store, "_job_cron", lambda workflow_id: None)
        assert _trigger_node_count(workflow_store.active("pulse")["definition"]) == 0
    migrated = workflow_store.active("pulse")["definition"]
    assert _trigger_node_count(migrated) == 1
    workflow_registry.validate_promotion("pulse", migrated)


def _trigger_node_count(definition):
    return sum(1 for node in definition["nodes"] if node["type"] == "trigger.schedule")


def test_seeded_pulse_definition_opens_with_a_trigger_at_the_head():
    definition = _definition()
    trigger = _trigger(definition)
    assert trigger["config"]["mode"] == "daily"
    assert trigger["config"]["time"] == "05:00"
    heads = {edge["to"] for edge in definition["edges"] if edge["from"] == trigger["id"]}
    assert heads == {"triage"}
    assert not [edge for edge in definition["edges"] if edge["to"] == trigger["id"]]
    workflow_registry.validate_definition("pulse", definition)
    workflow_registry.validate_promotion("pulse", definition)


def test_edge_into_a_trigger_is_rejected():
    definition = _definition()
    definition["edges"].append({"from": "inject", "to": _trigger(definition)["id"]})
    with pytest.raises(ValueError, match="cannot take an input"):
        workflow_registry.validate_definition("pulse", definition)


def test_second_trigger_is_rejected():
    definition = _definition()
    definition["nodes"].append({"id": "again", "type": "trigger.schedule", "config": {}})
    definition["edges"].append({"from": "again", "to": "triage"})
    with pytest.raises(ValueError, match="only one trigger"):
        workflow_registry.validate_definition("pulse", definition)


def test_trigger_must_point_at_the_path_head():
    definition = _definition()
    trigger_id = _trigger(definition)["id"]
    definition["edges"] = [edge for edge in definition["edges"] if edge["from"] != trigger_id]
    definition["edges"].append({"from": trigger_id, "to": "gates"})
    with pytest.raises(ValueError, match="start of the workflow"):
        workflow_registry.validate_definition("pulse", definition)


def test_triggerless_draft_saves_but_promotion_is_gated():
    definition = _definition()
    trigger_id = _trigger(definition)["id"]
    definition["nodes"] = [node for node in definition["nodes"] if node["id"] != trigger_id]
    definition["edges"] = [edge for edge in definition["edges"] if edge["from"] != trigger_id]
    workflow_registry.validate_definition("pulse", definition)
    with pytest.raises(ValueError, match="Add a Schedule trigger"):
        workflow_registry.validate_promotion("pulse", definition)
    draft = workflow_store.create_draft("pulse")
    saved = workflow_store.save_draft(draft["id"], definition)
    assert saved["definition"]["id"] == "pulse"
    with pytest.raises(ValueError, match="Add a Schedule trigger"):
        workflow_store.promote(draft["id"])
    assert workflow_store.active("pulse")["version"] == 1


def test_bad_trigger_time_is_rejected_on_save():
    definition = _definition()
    _trigger(definition)["config"] = {"mode": "daily", "time": "26:90"}
    with pytest.raises(ValueError, match="time of day"):
        workflow_registry.validate_definition("pulse", definition)


def test_stored_definitions_migrate_in_place(tmp_path):
    legacy = {
        "id": "pulse",
        "nodes": [{"id": "triage", "type": "pulse.triage"},
                  {"id": "gates", "type": "pulse.gates"},
                  {"id": "synthesis", "type": "pulse.synthesis"},
                  {"id": "claim_audit", "type": "pulse.claim_audit"},
                  {"id": "cover_art", "type": "pulse.cover_art", "config": {"style": "rotating"}},
                  {"id": "inject", "type": "pulse.inject"}],
        "edges": [{"from": "triage", "to": "gates"}, {"from": "gates", "to": "synthesis"},
                  {"from": "synthesis", "to": "claim_audit"}, {"from": "claim_audit", "to": "cover_art"},
                  {"from": "cover_art", "to": "inject"}],
        "positions": {"triage": {"x": 105, "y": 310}, "gates": {"x": 289, "y": 310},
                      "synthesis": {"x": 473, "y": 310}, "claim_audit": {"x": 657, "y": 310},
                      "cover_art": {"x": 841, "y": 310}, "inject": {"x": 1025, "y": 310}},
    }
    path = str(tmp_path / "legacy.db")
    workflow_store.DB_PATH = path
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE workflow_versions (
        id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, version INTEGER NOT NULL,
        state TEXT NOT NULL, definition TEXT NOT NULL, created_at INTEGER NOT NULL,
        promoted_at INTEGER, UNIQUE(workflow_id, version))""")
    conn.execute("INSERT INTO workflow_versions VALUES('v1','pulse',1,'active',?,0,0)",
                 (json.dumps(legacy),))
    conn.commit()
    conn.close()
    migrated = workflow_store.active("pulse")["definition"]
    trigger = _trigger(migrated)
    assert migrated["nodes"][0]["id"] == trigger["id"]
    assert {"from": trigger["id"], "to": "triage"} in migrated["edges"]
    position = migrated["positions"][trigger["id"]]
    assert position["y"] == 310
    assert position["x"] >= workflow_store.TRIGGER_MIN_X
    gap = migrated["positions"]["triage"]["x"] - position["x"]
    assert gap == workflow_store.TRIGGER_PLACEMENT_PITCH
    workflow_registry.validate_promotion("pulse", migrated)


def test_active_definition_mirrors_the_live_job_schedule():
    scheduler_store.set_override("pulse", cron="30 6 * * *")
    trigger = _trigger(_definition())
    assert trigger["config"] == {"mode": "daily", "time": "06:30"}


def test_promotion_updates_the_scheduler_job():
    draft = workflow_store.create_draft("pulse")
    definition = draft["definition"]
    _trigger(definition)["config"] = {"mode": "weekly", "weekday": "friday", "time": "08:15"}
    workflow_store.save_draft(draft["id"], definition)
    workflow_store.promote(draft["id"])
    assert scheduler.job_cron("pulse") == "15 8 * * 5"
    assert _trigger(_definition())["config"] == {"mode": "weekly", "weekday": "friday", "time": "08:15"}


def test_env_pinned_schedule_blocks_a_schedule_change(monkeypatch):
    monkeypatch.setenv("SCHEDULE_PULSE", "0 5 * * *")
    draft = workflow_store.create_draft("pulse")
    definition = draft["definition"]
    _trigger(definition)["config"] = {"mode": "daily", "time": "09:00"}
    workflow_store.save_draft(draft["id"], definition)
    with pytest.raises(ValueError, match="pinned"):
        workflow_store.promote(draft["id"])
    assert workflow_store.active("pulse")["version"] == 1


def test_env_pinned_schedule_allows_promotion_without_a_change(monkeypatch):
    monkeypatch.setenv("SCHEDULE_PULSE", "0 5 * * *")
    draft = workflow_store.create_draft("pulse")
    promoted = workflow_store.promote(draft["id"])
    assert promoted["state"] == "active"


def test_executor_strips_triggers_and_runs_the_body():
    ran = []

    async def _head(node, ctx):
        if ran:
            return None
        ran.append("pull")
        return [{"n": 1}]

    async def _tail(node, items, ctx):
        ran.append(f"tail:{len(items)}")
        return items

    workflow_executor.register("test.head", pull=_head)
    workflow_executor.register("test.tail", run=_tail)
    try:
        definition = {
            "id": "test-flow",
            "nodes": [{"id": "schedule", "type": "trigger.schedule",
                       "config": {"mode": "daily", "time": "05:00"}},
                      {"id": "head", "type": "test.head"},
                      {"id": "tail", "type": "test.tail"}],
            "edges": [{"from": "schedule", "to": "head"}, {"from": "head", "to": "tail"}],
        }
        ctx = workflow_executor.RunContext(definition)
        asyncio.run(workflow_executor.execute(definition, ctx))
    finally:
        workflow_executor.NODE_IMPLS.pop("test.head", None)
        workflow_executor.NODE_IMPLS.pop("test.tail", None)
    assert ran == ["pull", "tail:1"]
