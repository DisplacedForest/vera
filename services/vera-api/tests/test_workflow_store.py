import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
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


def test_run_records_node_outputs():
    workflow_store.start_run("pulse", "run-1")
    output = {"rounds": [{"proposed": ["a"]}], "topics": ["a"], "injected": ["a"],
              "gates": {"dedup": 0}, "items": [{"cover_generated": True}]}
    workflow_store.record_node_runs("run-1", output)
    workflow_store.finish_run("run-1", "ok", output)
    run = workflow_store.latest_run("pulse")
    assert run["state"] == "ok"
    assert [node["id"] for node in run["nodes"]] == [
        "triage", "gates", "synthesis", "claim_audit", "cover_art", "inject"]
    assert next(node for node in run["nodes"] if node["id"] == "cover_art")["output"]["generated"] == 1
