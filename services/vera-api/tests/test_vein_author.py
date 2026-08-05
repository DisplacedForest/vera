import asyncio
import json
import os

import pytest
from fastapi import HTTPException

from routers import pulse, vein_builder, vein_defs, vein_engine, vein_store


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(pulse, "VERA_BASE", "http://llm.example/v1")
    monkeypatch.setattr(pulse, "MODEL", "test-model")
    monkeypatch.setattr(vein_defs, "CUSTOM_DIR", str(tmp_path / "veins.d"))
    monkeypatch.setattr(vein_store, "PATH", str(tmp_path / "veins.json"))
    yield


GOOD_DRAFT = {
    "kind": "lumber_prices", "label": "Lumber prices", "icon": "chart.line.uptrend.xyaxis",
    "pipeline": [
        {"block": "http_fetch", "params": {"url": "https://q.example/lumber.json",
                                           "extract": "price"}},
        {"block": "trip_band", "params": {"hi": 700}},
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


def _author(**kw):
    return asyncio.run(vein_builder.author(vein_builder.AuthorRequest(**kw)))


def test_draft_returns_validated_draft_and_summary(monkeypatch):
    _script(monkeypatch, [json.dumps({
        "reply": "Here you go.", "draft": GOOD_DRAFT, "recommended": [], "done": True})])
    out = _author(mode="draft", request="keep an eye on lumber prices")
    assert out["ok"] is True
    assert out["draft"]["kind"] == "lumber_prices"
    assert "every 30 minutes" in out["summary"]
    assert "http fetch" in out["summary"]
    assert not os.path.isdir(vein_defs.CUSTOM_DIR)
    assert vein_store.load() == {}


def test_draft_invalid_repairs_once(monkeypatch):
    bad = {**GOOD_DRAFT}
    bad.pop("schedule")
    calls = _script(monkeypatch, [
        json.dumps({"reply": "Draft.", "draft": bad, "recommended": [], "done": True}),
        json.dumps({"reply": "Fixed.", "draft": GOOD_DRAFT, "recommended": [], "done": True}),
    ])
    out = _author(mode="draft", request="watch lumber")
    assert out["ok"] is True and out["draft"]["schedule"] == "*/30 * * * *"
    assert len(calls) == 2
    assert "failed validation" in calls[1][-1]["content"]


def test_draft_unrepairable_surfaces_problems(monkeypatch):
    bad = {**GOOD_DRAFT}
    bad.pop("schedule")
    _script(monkeypatch, [
        json.dumps({"reply": "Draft.", "draft": bad, "recommended": [], "done": True})])
    out = _author(mode="draft", request="watch lumber")
    assert out["ok"] is False and out["draft"] is None
    assert out["problems"] and "schedule" in out["problems"][0]


def test_draft_malformed_json_repairs_through_structured(monkeypatch):
    calls = _script(monkeypatch, [
        "sure, watching lumber now!",
        json.dumps({"reply": "Draft.", "draft": GOOD_DRAFT, "recommended": [], "done": True}),
    ])
    out = _author(mode="draft", request="watch lumber")
    assert out["ok"] is True and out["draft"]["kind"] == "lumber_prices"
    assert len(calls) == 2
    assert "JSON object" in calls[1][-1]["content"]


def test_draft_without_model_degrades_clean(monkeypatch):
    monkeypatch.setattr(pulse, "VERA_BASE", "")
    out = _author(mode="draft", request="watch lumber")
    assert out["disabled"] and out["ok"] is False


def test_draft_needs_a_request():
    with pytest.raises(HTTPException) as e:
        _author(mode="draft", request="   ")
    assert e.value.status_code == 422


def test_save_writes_definition_enables_and_schedules():
    out = _author(mode="save", draft=GOOD_DRAFT)
    assert out["ok"] is True and out["enabled"] is True
    assert os.path.isfile(os.path.join(vein_defs.CUSTOM_DIR, "lumber_prices.json"))
    assert vein_store.load()["lumber_prices"]["enabled"] is True
    assert "every 30 minutes" in out["summary"]
    assert "vein_lumber_prices" in vein_engine.dynamic_jobs()


def test_save_rejects_existing_kind():
    _author(mode="save", draft=GOOD_DRAFT)
    with pytest.raises(HTTPException) as e:
        _author(mode="save", draft=GOOD_DRAFT)
    assert e.value.status_code == 409


def test_save_rejects_invalid_draft():
    bad = {**GOOD_DRAFT}
    bad.pop("schedule")
    with pytest.raises(HTTPException) as e:
        _author(mode="save", draft=bad)
    assert e.value.status_code == 422
    assert not os.path.isdir(vein_defs.CUSTOM_DIR)


def test_save_needs_a_draft():
    with pytest.raises(HTTPException) as e:
        _author(mode="save")
    assert e.value.status_code == 422


def test_save_over_cap_saves_but_reports_not_enabled(monkeypatch):
    from routers import pulse_veins
    monkeypatch.setattr(pulse_veins, "MAX_ACTIVE", 0)
    out = _author(mode="save", draft=GOOD_DRAFT)
    assert out["ok"] is True and out["enabled"] is False
    assert "cap" in out["detail"]
    assert os.path.isfile(os.path.join(vein_defs.CUSTOM_DIR, "lumber_prices.json"))
    assert not vein_store.load()["lumber_prices"].get("enabled")


def test_unknown_mode_is_rejected():
    with pytest.raises(HTTPException) as e:
        _author(mode="commit")
    assert e.value.status_code == 422


def test_cadence_phrases_are_deterministic():
    assert vein_builder._cadence("*/15 * * * *") == "every 15 minutes"
    assert vein_builder._cadence("0 * * * *") == "every hour"
    assert vein_builder._cadence("0 */6 * * *") == "every 6 hours"
    assert vein_builder._cadence("30 7 * * *") == "every day at 7:30"
    assert vein_builder._cadence("0 9 * * 1") == "every Monday at 9:00"
    assert vein_builder._cadence("0 9 1 * *") == "on the schedule '0 9 1 * *'"
