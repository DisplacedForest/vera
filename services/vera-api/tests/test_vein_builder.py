import asyncio
import json

import pytest

from routers import pulse, pulse_store, vein_builder, vein_engine_store


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(pulse_store, "DB_PATH", str(tmp_path / "pulse.db"))
    monkeypatch.setattr(vein_engine_store, "DB_PATH", str(tmp_path / "engine.db"))
    monkeypatch.setattr(pulse, "VERA_BASE", "http://llm.example/v1")
    monkeypatch.setattr(pulse, "MODEL", "test-model")
    yield


GOOD_DRAFT = {
    "kind": "river_gauge", "label": "River gauge", "icon": "water.waves",
    "pipeline": [
        {"block": "http_fetch", "params": {"url": "https://g.example/x.json",
                                           "extract": "level"}},
        {"block": "trip_band", "params": {"hi": 21.5}},
    ],
    "schedule": "*/30 * * * *",
}


def _script(monkeypatch, replies):
    calls = []

    async def fake(messages, **kw):
        calls.append(messages)
        return replies[min(len(calls) - 1, len(replies) - 1)]
    monkeypatch.setattr(vein_builder, "_vera", fake)
    return calls


def _turn(messages=None):
    return asyncio.run(vein_builder.turn(
        vein_builder.TurnRequest(messages=messages or [{"role": "user", "content": "watch the river gauge"}])))


def test_prompt_embeds_live_schema():
    prompt = vein_builder.builder_prompt()
    assert "<<SCHEMA>>" not in prompt
    assert "^[a-z][a-z0-9_]*$" in prompt
    assert "producer_jobs" in prompt


def test_fallback_prompt_also_embeds_schema(monkeypatch):
    monkeypatch.setattr(vein_builder, "BUILDER_PATH", "/nonexistent/BUILDER.md")
    prompt = vein_builder.builder_prompt()
    assert "^[a-z][a-z0-9_]*$" in prompt


def test_valid_draft_round_trips(monkeypatch):
    _script(monkeypatch, [json.dumps({
        "reply": "Here is the draft.", "draft": GOOD_DRAFT,
        "recommended": ["http_fetch", "trip_band"], "done": True})])
    out = _turn()
    assert out["valid"] is True
    assert out["draft"]["kind"] == "river_gauge"
    assert out["recommended"] == ["http_fetch", "trip_band"]
    assert out["done"] is True and out["problems"] == []


def test_question_turn_has_no_draft(monkeypatch):
    _script(monkeypatch, [json.dumps({
        "reply": "Which gauge endpoint should I read?", "draft": None,
        "recommended": [], "done": False})])
    out = _turn()
    assert out["valid"] is False and out["draft"] is None and out["problems"] == []
    assert "endpoint" in out["reply"]


def test_invalid_draft_repairs_once(monkeypatch):
    bad = {**GOOD_DRAFT}
    bad.pop("schedule")
    calls = _script(monkeypatch, [
        json.dumps({"reply": "Draft ready.", "draft": bad, "recommended": [], "done": True}),
        json.dumps({"reply": "Fixed.", "draft": GOOD_DRAFT, "recommended": [], "done": True}),
    ])
    out = _turn()
    assert out["valid"] is True and out["draft"]["schedule"] == "*/30 * * * *"
    assert len(calls) == 2
    assert "failed validation" in calls[1][-1]["content"]


def test_unrepairable_draft_surfaces_problems(monkeypatch):
    bad = {**GOOD_DRAFT}
    bad.pop("schedule")
    _script(monkeypatch, [
        json.dumps({"reply": "Draft ready.", "draft": bad, "recommended": [], "done": True})])
    out = _turn()
    assert out["valid"] is False and out["draft"] is None
    assert out["problems"] and "schedule" in out["problems"][0]


def test_unknown_block_surfaces_pipeline_problem(monkeypatch):
    bad = {**GOOD_DRAFT, "pipeline": [{"block": "teleport"}]}
    _script(monkeypatch, [
        json.dumps({"reply": "Draft.", "draft": bad, "recommended": ["teleport"], "done": True})])
    out = _turn()
    assert out["valid"] is False
    assert any("teleport" in p for p in out["problems"])


def test_dry_run_returns_would_post_and_steps(monkeypatch):
    from routers import vein_engine

    async def fake_get(url):
        return 200, json.dumps({"level": 25})
    monkeypatch.setattr(vein_engine, "_get", fake_get)
    out = asyncio.run(vein_builder.dry_run(vein_builder.DryRunRequest(definition=GOOD_DRAFT)))
    assert out["ok"] is True
    assert len(out["would_post"]) == 1 and out["would_post"][0]["severity"] == "alert"
    assert [s["block"] for s in out["steps"]] == ["http_fetch", "trip_band"]
    assert pulse_store.list_cards() == []


def test_dry_run_invalid_definition_reports_errors():
    out = asyncio.run(vein_builder.dry_run(
        vein_builder.DryRunRequest(definition={"kind": "x"})))
    assert out["ok"] is False and out["errors"]


def test_unconfigured_model_degrades_clean(monkeypatch):
    monkeypatch.setattr(pulse, "VERA_BASE", "")
    turn_out = _turn()
    dry_out = asyncio.run(vein_builder.dry_run(vein_builder.DryRunRequest(definition=GOOD_DRAFT)))
    assert turn_out["disabled"] and dry_out["disabled"]


def test_status_probe_reports_configured(monkeypatch):
    assert asyncio.run(vein_builder.status()) == {"configured": True}
    monkeypatch.setattr(pulse, "VERA_BASE", "")
    assert asyncio.run(vein_builder.status()) == {"configured": False}
