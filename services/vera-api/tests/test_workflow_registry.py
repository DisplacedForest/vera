import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from routers import agentic
from routers import vein_engine
from routers import workflow_registry
from routers import workflow_store


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    monkeypatch.setattr(workflow_store, "DB_PATH", str(tmp_path / "workflows.db"))


@pytest.fixture
def stub_node():
    async def _run(items, params, ctx):
        return items
    vein_engine.register("card_stub", _run, node={
        "label": "Card stub", "icon": "circle", "tint": "accent",
        "category": "transform", "insertable": True,
        "config_schema": {"limit": {"type": "number", "min": 1, "max": 10, "default": 5}},
    })
    yield "card_stub"
    vein_engine.BLOCKS.pop("card_stub", None)
    vein_engine.NODE_SPECS.pop("card_stub", None)


def _pulse_definition():
    return workflow_store.active("pulse")["definition"]


def _insert_between(definition, upstream, downstream, node):
    definition["nodes"].append(node)
    definition["edges"] = [edge for edge in definition["edges"]
                           if edge != {"from": upstream, "to": downstream}]
    definition["edges"].extend([
        {"from": upstream, "to": node["id"]},
        {"from": node["id"], "to": downstream},
    ])


def test_catalog_serves_every_pulse_node_with_schema_and_category():
    entries = {entry["type"]: entry for entry in workflow_registry.catalog()}
    for node_type in workflow_registry.PULSE_SPECS:
        assert entries[node_type]["category"]
        assert "config_schema" in entries[node_type]
    assert entries["pulse.cover_art"]["config_schema"]["style"]["options"] == [
        "rotating", "photographic", "illustrated", "editorial"]
    assert entries["pulse.visual_review"]["config_schema"]["threshold"]["max"] == 1


def test_catalog_includes_vein_blocks_as_non_insertable_nodes():
    entries = {entry["type"]: entry for entry in workflow_registry.catalog()}
    assert entries["http_fetch"]["category"] == "enrich"
    assert entries["http_fetch"]["insertable"] is False


def test_catalog_endpoint_returns_nodes_and_profile():
    payload = asyncio.run(agentic.workflow_catalog("pulse"))
    assert {entry["type"] for entry in payload["nodes"]} >= set(workflow_registry.PULSE_SPECS)
    assert payload["profile"]["spine"][0] == "pulse.triage"
    assert payload["profile"]["insertable_categories"] == ["transform", "enrich", "notify"]


def test_catalog_endpoint_rejects_unknown_workflow():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        asyncio.run(agentic.workflow_catalog("nope"))
    assert exc.value.status_code == 404


def test_seeded_pulse_definition_is_valid():
    workflow_registry.validate_definition("pulse", _pulse_definition())


@pytest.mark.parametrize("upstream,downstream", [
    ("triage", "gates"), ("gates", "synthesis"), ("synthesis", "claim_audit"),
    ("claim_audit", "cover_art"), ("cover_art", "inject"),
])
def test_insertable_node_fits_between_each_stage_pair(stub_node, upstream, downstream):
    definition = _pulse_definition()
    _insert_between(definition, upstream, downstream,
                    {"id": "stub", "type": stub_node, "label": "Stub", "config": {"limit": 3}})
    workflow_registry.validate_definition("pulse", definition)


def test_insertable_node_saves_through_the_draft_path(stub_node):
    draft = workflow_store.create_draft("pulse")
    definition = draft["definition"]
    _insert_between(definition, "gates", "synthesis",
                    {"id": "stub", "type": stub_node, "label": "Stub"})
    saved = workflow_store.save_draft(draft["id"], definition)
    assert any(node["id"] == "stub" for node in saved["definition"]["nodes"])


def test_missing_core_stage_is_named():
    definition = _pulse_definition()
    definition["nodes"] = [node for node in definition["nodes"] if node["id"] != "claim_audit"]
    definition["edges"] = [edge for edge in definition["edges"]
                           if "claim_audit" not in (edge["from"], edge["to"])]
    definition["edges"].append({"from": "synthesis", "to": "cover_art"})
    with pytest.raises(ValueError, match="Claim audit"):
        workflow_registry.validate_definition("pulse", definition)


def test_duplicate_core_stage_is_rejected():
    definition = _pulse_definition()
    _insert_between(definition, "gates", "synthesis",
                    {"id": "gates_2", "type": "pulse.gates", "label": "Gates again"})
    with pytest.raises(ValueError, match="more than once"):
        workflow_registry.validate_definition("pulse", definition)


def test_reordered_core_stages_are_rejected():
    definition = _pulse_definition()
    definition["edges"] = [
        {"from": "triage", "to": "synthesis"},
        {"from": "synthesis", "to": "gates"},
        {"from": "gates", "to": "claim_audit"},
        {"from": "claim_audit", "to": "cover_art"},
        {"from": "cover_art", "to": "inject"},
    ]
    with pytest.raises(ValueError, match="order"):
        workflow_registry.validate_definition("pulse", definition)


def test_unpaired_review_is_rejected():
    definition = _pulse_definition()
    _insert_between(definition, "cover_art", "inject",
                    {"id": "visual_review", "type": "pulse.visual_review", "label": "Visual review"})
    with pytest.raises(ValueError, match="added together"):
        workflow_registry.validate_definition("pulse", definition)


def test_review_pair_before_cover_art_is_rejected():
    definition = _pulse_definition()
    _insert_between(definition, "gates", "synthesis",
                    {"id": "visual_review", "type": "pulse.visual_review", "label": "Visual review"})
    _insert_between(definition, "visual_review", "synthesis",
                    {"id": "cover_retry", "type": "pulse.cover_retry", "label": "Retry"})
    with pytest.raises(ValueError, match="between"):
        workflow_registry.validate_definition("pulse", definition)


def test_non_insertable_block_is_rejected_for_pulse():
    definition = _pulse_definition()
    _insert_between(definition, "gates", "synthesis",
                    {"id": "fetch", "type": "http_fetch", "label": "Fetch"})
    with pytest.raises(ValueError, match="cannot be added"):
        workflow_registry.validate_definition("pulse", definition)


def test_branching_graph_is_rejected_for_pulse(stub_node):
    definition = _pulse_definition()
    definition["nodes"].append({"id": "stub", "type": stub_node, "label": "Stub"})
    definition["edges"].append({"from": "gates", "to": "stub"})
    definition["edges"].append({"from": "stub", "to": "synthesis"})
    with pytest.raises(ValueError, match="single connected path"):
        workflow_registry.validate_definition("pulse", definition)


def test_out_of_bounds_config_names_the_field():
    definition = _pulse_definition()
    _insert_between(definition, "cover_art", "inject",
                    {"id": "visual_review", "type": "pulse.visual_review", "label": "Visual review",
                     "config": {"threshold": 2}})
    _insert_between(definition, "visual_review", "inject",
                    {"id": "cover_retry", "type": "pulse.cover_retry", "label": "Retry"})
    with pytest.raises(ValueError, match="threshold"):
        workflow_registry.validate_definition("pulse", definition)


def test_unknown_config_field_is_rejected():
    definition = _pulse_definition()
    for node in definition["nodes"]:
        if node["id"] == "cover_art":
            node["config"] = {"style": "rotating", "seed": 4}
    with pytest.raises(ValueError, match="seed"):
        workflow_registry.validate_definition("pulse", definition)


def test_choice_config_rejects_unknown_option():
    definition = _pulse_definition()
    for node in definition["nodes"]:
        if node["id"] == "cover_art":
            node["config"] = {"style": "vaporwave"}
    with pytest.raises(ValueError, match="style"):
        workflow_registry.validate_definition("pulse", definition)


def _definition_with_review_pair(retry_config=None, review_config=None):
    definition = _pulse_definition()
    review = {"id": "visual_review", "type": "pulse.visual_review", "label": "Visual review"}
    if review_config is not None:
        review["config"] = review_config
    _insert_between(definition, "cover_art", "inject", review)
    retry = {"id": "cover_retry", "type": "pulse.cover_retry", "label": "Retry"}
    if retry_config is not None:
        retry["config"] = retry_config
    _insert_between(definition, "visual_review", "inject", retry)
    return definition


@pytest.mark.parametrize("attempts", [True, False])
def test_boolean_retry_cap_is_rejected(attempts):
    definition = _definition_with_review_pair(retry_config={"max_attempts": attempts})
    with pytest.raises(ValueError, match="max_attempts"):
        workflow_registry.validate_definition("pulse", definition)


@pytest.mark.parametrize("config", [[], None, "config"])
def test_non_object_config_is_rejected(config):
    definition = _pulse_definition()
    for node in definition["nodes"]:
        if node["id"] == "cover_art":
            node["config"] = config
    with pytest.raises(ValueError, match="must be an object"):
        workflow_registry.validate_definition("pulse", definition)


@pytest.mark.parametrize("threshold", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_threshold_is_rejected(threshold):
    definition = _definition_with_review_pair(review_config={"threshold": threshold})
    with pytest.raises(ValueError, match="threshold"):
        workflow_registry.validate_definition("pulse", definition)


@pytest.mark.parametrize("threshold", [0, 1, 0.5])
def test_boundary_thresholds_are_accepted(threshold):
    definition = _definition_with_review_pair(review_config={"threshold": threshold})
    workflow_registry.validate_definition("pulse", definition)


def test_generic_profile_accepts_a_dag(stub_node):
    definition = {"id": "custom", "nodes": [
        {"id": "a", "type": stub_node, "label": "A"},
        {"id": "b", "type": stub_node, "label": "B"},
        {"id": "c", "type": stub_node, "label": "C"},
    ], "edges": [
        {"from": "a", "to": "b"},
        {"from": "a", "to": "c"},
        {"from": "b", "to": "c"},
    ]}
    workflow_registry.validate_definition("custom", definition)


def test_generic_profile_rejects_a_cycle(stub_node):
    definition = {"id": "custom", "nodes": [
        {"id": "a", "type": stub_node, "label": "A"},
        {"id": "b", "type": stub_node, "label": "B"},
    ], "edges": [
        {"from": "a", "to": "b"},
        {"from": "b", "to": "a"},
    ]}
    with pytest.raises(ValueError, match="cycle"):
        workflow_registry.validate_definition("custom", definition)


def test_generic_profile_rejects_unregistered_types():
    definition = {"id": "custom", "nodes": [{"id": "a", "type": "mystery", "label": "A"}], "edges": []}
    with pytest.raises(ValueError, match="unsupported"):
        workflow_registry.validate_definition("custom", definition)
