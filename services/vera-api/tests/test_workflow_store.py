import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from routers import agentic
from routers import workflow_store


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    monkeypatch.setattr(workflow_store, "DB_PATH", str(tmp_path / "workflows.db"))


def test_pulse_baseline_is_active_and_versioned():
    workflow = workflow_store.active("pulse")
    assert workflow["version"] == 1
    assert workflow["state"] == "active"
    assert [node["id"] for node in workflow["definition"]["nodes"]] == [
        "triage", "gates", "synthesis", "claim_audit", "cover_art", "inject"]


def test_draft_can_be_saved_and_promoted():
    draft = workflow_store.create_draft("pulse")
    definition = draft["definition"]
    definition["nodes"].insert(-1, {"id": "visual_review", "type": "pulse.visual_review", "label": "Visual review"})
    definition["nodes"].insert(-1, {"id": "cover_retry", "type": "pulse.cover_retry", "label": "Retry"})
    definition["edges"] = [edge for edge in definition["edges"] if edge != {"from": "cover_art", "to": "inject"}]
    definition["edges"].extend([
        {"from": "cover_art", "to": "visual_review"},
        {"from": "visual_review", "to": "cover_retry"},
        {"from": "cover_retry", "to": "inject"},
    ])
    saved = workflow_store.save_draft(draft["id"], definition)
    assert saved["state"] == "draft"
    active = workflow_store.promote(draft["id"])
    assert active["state"] == "active"
    assert active["version"] == 2
    assert workflow_store.active("pulse")["id"] == active["id"]


def test_draft_rejects_an_unpaired_review_node():
    draft = workflow_store.create_draft("pulse")
    definition = draft["definition"]
    definition["nodes"].insert(-1, {"id": "visual_review", "type": "pulse.visual_review", "label": "Visual review"})
    with pytest.raises(ValueError, match="added together"):
        workflow_store.save_draft(draft["id"], definition)


def test_run_records_live_node_rows():
    workflow_store.start_run("pulse", "run-1")
    node_run = workflow_store.start_node("run-1", "triage", input={"items": 0})
    workflow_store.finish_node(node_run, "ok", {"items": 3, "rounds": 1}, input={"items": 0})
    workflow_store.finish_run("run-1", "ok", {"injected": ["a"]})
    run = workflow_store.latest_run("pulse")
    assert run["state"] == "ok"
    [row] = run["nodes"]
    assert row["id"] == "triage" and row["state"] == "ok"
    assert row["input"] == {"items": 0}
    assert row["output"] == {"items": 3, "rounds": 1}
    assert row["started_at"] and row["finished_at"] and row["started_at"] <= row["finished_at"]


def test_run_records_the_version_selected_at_start():
    workflow_store.start_run("pulse", "run-1")
    draft = workflow_store.create_draft("pulse")
    definition = draft["definition"]
    definition["nodes"].insert(-1, {"id": "visual_review", "type": "pulse.visual_review", "label": "Visual review"})
    definition["nodes"].insert(-1, {"id": "cover_retry", "type": "pulse.cover_retry", "label": "Retry"})
    definition["edges"] = [edge for edge in definition["edges"] if edge != {"from": "cover_art", "to": "inject"}]
    definition["edges"].extend([
        {"from": "cover_art", "to": "visual_review"},
        {"from": "visual_review", "to": "cover_retry"},
        {"from": "cover_retry", "to": "inject"},
    ])
    workflow_store.save_draft(draft["id"], definition)
    promoted = workflow_store.promote(draft["id"])
    run = workflow_store.latest_run("pulse")
    assert run["workflow_version_id"] != promoted["id"]
    pinned = workflow_store.get_version(run["workflow_version_id"])
    assert [node["id"] for node in pinned["definition"]["nodes"]] == [
        "triage", "gates", "synthesis", "claim_audit", "cover_art", "inject"]
    assert run["version"]["id"] == run["workflow_version_id"]
    assert run["version"]["definition"] == pinned["definition"]


def test_draft_validates_visual_controls():
    draft = workflow_store.create_draft("pulse")
    definition = draft["definition"]
    definition["nodes"].insert(-1, {"id": "visual_review", "type": "pulse.visual_review", "label": "Visual review", "config": {"threshold": 0.9}})
    definition["nodes"].insert(-1, {"id": "cover_retry", "type": "pulse.cover_retry", "label": "Retry", "config": {"max_attempts": 1}})
    definition["edges"] = [edge for edge in definition["edges"] if edge != {"from": "cover_art", "to": "inject"}]
    definition["edges"].extend([{"from": "cover_art", "to": "visual_review"}, {"from": "visual_review", "to": "cover_retry"}, {"from": "cover_retry", "to": "inject"}])
    assert workflow_store.save_draft(draft["id"], definition)["definition"]["nodes"][-3]["config"]["threshold"] == 0.9


def test_visual_evidence_keeps_each_card_attempt():
    workflow_store.start_run("pulse", "run-1")
    workflow_store.record_visual_run("run-1", "card-1", "one", {"accept": False}, 0, "retrying")
    workflow_store.record_visual_run("run-1", "card-1", "two", {"accept": False}, 1, "retried")
    workflow_store.finish_run("run-1", "ok", {})
    assert [item["retry_count"] for item in workflow_store.latest_run("pulse")["visual_runs"]] == [0, 1]


def test_malformed_nodes_are_a_controlled_bad_request():
    draft = workflow_store.create_draft("pulse")
    malformed = {**draft["definition"], "nodes": [1]}
    with pytest.raises(HTTPException) as exc:
        import asyncio
        asyncio.run(agentic.update_workflow_draft(draft["id"], agentic.WorkflowDefinitionUpdate(definition=malformed)))
    assert exc.value.status_code == 400
