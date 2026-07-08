"""Built-in scheduler unit tests. Standalone — run: pytest tests/test_scheduler.py

Covers the deterministic core: env > db > registry precedence, env locking,
next-fire computation, outcome recording, and failure isolation in _fire().
The loop itself is a thin poll over these parts.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from routers import vein_store  # noqa: E402
from routers import scheduler as sch  # noqa: E402
from routers import scheduler_store as store  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "scheduler.db"))
    monkeypatch.setattr(vein_store, "PATH", str(tmp_path / "veins.json"))
    # Scrub any SCHEDULE_* env so precedence tests start from registry defaults.
    for k in list(os.environ):
        if k.startswith("SCHEDULE_"):
            monkeypatch.delenv(k)
    yield


def _open_weather_vein(monkeypatch):
    """Open the weather job's vein gate: vein enabled + its coordinate requirement met."""
    monkeypatch.setenv("WEATHER_LAT", "39.0")
    monkeypatch.setenv("WEATHER_LON", "-95.0")
    vein_store.update("weather", enabled=True)


def test_registry_defaults_apply():
    j = sch._effective("pulse", None)
    assert j["cron"] == "0 5 * * *"
    assert j["enabled"] is True
    assert j["env_locked"] is False
    assert j["last_run"] is None


def test_db_override_beats_registry():
    store.set_override("pulse", cron="0 6 * * *", enabled=False)
    j = sch._effective("pulse", store.overrides()["pulse"])
    assert j["cron"] == "0 6 * * *"
    assert j["enabled"] is False


def test_env_beats_db(monkeypatch):
    store.set_override("pulse", cron="0 6 * * *", enabled=True)
    monkeypatch.setenv("SCHEDULE_PULSE", "0 7 * * *")
    monkeypatch.setenv("SCHEDULE_PULSE_ENABLED", "false")
    j = sch._effective("pulse", store.overrides()["pulse"])
    assert j["cron"] == "0 7 * * *"
    assert j["enabled"] is False
    assert j["env_locked"] is True


def test_jobs_view_covers_registry_with_next_runs():
    view = sch.jobs_view()
    assert {j["id"] for j in view} == set(sch._registry())
    assert set(sch.REGISTRY) <= {j["id"] for j in view}
    for j in view:
        if j["enabled"] and sch.ENABLED:
            assert j["next_run"] is not None


def test_outcome_recording():
    store.record_outcome("vein_weather", True, "ok: 0 concerns")
    j = sch._effective("vein_weather", store.overrides()["vein_weather"])
    assert j["last_run"]["ok"] is True
    assert "concerns" in j["last_run"]["detail"]


def test_fire_refuses_while_vein_gate_closed(monkeypatch):
    fired = []

    async def handler():
        fired.append(True)
        return {"ok": True}

    monkeypatch.setitem(sch.REGISTRY, "vein_weather", ("Weather vein run", "0 */6 * * *", handler))
    asyncio.run(sch._fire("vein_weather"))  # weather vein off -> the gate refuses the fire
    assert not fired


def test_fire_failure_is_isolated_and_recorded(monkeypatch):
    async def boom():
        raise RuntimeError("collector exploded")

    _open_weather_vein(monkeypatch)
    monkeypatch.setitem(sch.REGISTRY, "weather", ("Weather check", "0 */6 * * *", boom))
    asyncio.run(sch._fire("weather"))
    row = store.overrides()["weather"]
    assert row["last_ok"] == 0
    assert "collector exploded" in row["last_detail"]


def test_fire_skips_overlapping_run(monkeypatch):
    async def ok():
        return {"ok": True}

    _open_weather_vein(monkeypatch)
    monkeypatch.setitem(sch.REGISTRY, "weather", ("Weather check", "0 */6 * * *", ok))
    sch._running.add("weather")
    try:
        asyncio.run(sch._fire("weather"))
        assert "still in progress" in store.overrides()["weather"]["last_detail"]
    finally:
        sch._running.discard("weather")


def _save_pipeline_vein(monkeypatch, tmp_path, kind="rivergauge"):
    from routers import vein_defs
    monkeypatch.setattr(vein_defs, "CUSTOM_DIR", str(tmp_path / "veins.d"))
    vein_defs.save_custom({
        "kind": kind, "label": "River gauge", "icon": "water.waves",
        "pipeline": [
            {"block": "http_fetch", "params": {"url": "https://g.example/x.json",
                                               "extract": "level"}},
            {"block": "trip_band", "params": {"hi": 21.5}},
        ],
        "schedule": "*/30 * * * *",
    })
    return kind


def test_pipeline_definition_surfaces_as_dynamic_job(monkeypatch, tmp_path):
    _save_pipeline_vein(monkeypatch, tmp_path)
    view = {j["id"]: j for j in sch.jobs_view()}
    job = view["vein_rivergauge"]
    assert job["label"] == "River gauge vein run"
    assert job["cron"] == "*/30 * * * *"
    assert job["enabled"] is False
    assert "vein is off" in (job["gated"] or "")


def test_dynamic_job_env_pin_applies(monkeypatch, tmp_path):
    _save_pipeline_vein(monkeypatch, tmp_path)
    monkeypatch.setenv("SCHEDULE_VEIN_RIVERGAUGE", "0 9 * * *")
    j = sch._effective("vein_rivergauge", None)
    assert j["cron"] == "0 9 * * *"


def test_dynamic_job_vanishes_with_its_definition(monkeypatch, tmp_path):
    from routers import vein_defs
    _save_pipeline_vein(monkeypatch, tmp_path)
    assert "vein_rivergauge" in sch._registry()
    vein_defs.delete_custom("rivergauge")
    assert "vein_rivergauge" not in sch._registry()


def test_fire_records_outcome_when_definition_is_gone(monkeypatch, tmp_path):
    _save_pipeline_vein(monkeypatch, tmp_path)
    monkeypatch.setattr(sch, "_gate_reason", lambda _id: None)
    from routers import vein_defs
    vein_defs.delete_custom("rivergauge")
    asyncio.run(sch._fire("vein_rivergauge"))
    assert "definition is gone" in store.overrides()["vein_rivergauge"]["last_detail"]
