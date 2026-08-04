import asyncio

import pytest

from routers import model_client as mc


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    from routers import integrations_store
    monkeypatch.setattr(integrations_store, "PATH", str(tmp_path / "integrations.json"), raising=False)
    for name in ("VERA_BASE", "VERA_MODEL", "DREAM_BASE", "DREAM_MODEL",
                 "VERA_THINK_KWARGS_ON", "VERA_THINK_KWARGS_OFF", "VERA_CHAT_TEMPLATE_KWARGS"):
        monkeypatch.delenv(name, raising=False)
    yield


def _capture(monkeypatch, content="hi"):
    calls = {}

    async def post(url, body, timeout):
        calls["url"], calls["body"], calls["timeout"] = url, body, timeout
        return {"choices": [{"message": {"content": content, "tool_calls": []}}]}

    monkeypatch.setattr(mc, "_post", post)
    return calls


def test_main_config_resolves_env(monkeypatch):
    monkeypatch.setenv("VERA_BASE", "http://llm.example/v1/")
    monkeypatch.setenv("VERA_MODEL", "main-model")
    c = mc.config("main")
    assert c == {"base": "http://llm.example/v1", "model": "main-model", "timeout": 300}
    assert mc.configured("main") and mc.unavailable_reason("main") is None


def test_unconfigured_main_raises_the_structured_reason(monkeypatch):
    reason = mc.unavailable_reason("main")
    assert reason == "no model is configured. Set VERA_BASE and VERA_MODEL."
    with pytest.raises(mc.ModelUnavailable) as e:
        asyncio.run(mc.complete([{"role": "user", "content": "x"}]))
    assert str(e.value) == reason


def test_main_payload_carries_think_kwargs(monkeypatch):
    monkeypatch.setenv("VERA_BASE", "http://llm.example/v1")
    monkeypatch.setenv("VERA_MODEL", "m")
    monkeypatch.setenv("VERA_THINK_KWARGS_ON", '{"enable_thinking": true}')
    calls = _capture(monkeypatch)
    out = asyncio.run(mc.complete_text([{"role": "user", "content": "x"}],
                                       temperature=0.2, think="on"))
    assert out == "hi"
    body = calls["body"]
    assert body["model"] == "m" and body["stream"] is False and body["temperature"] == 0.2
    assert body["chat_template_kwargs"] == {"enable_thinking": True}
    assert calls["url"] == "http://llm.example/v1/chat/completions"
    assert calls["timeout"] == 300


def test_main_payload_is_pure_openai_without_kwargs(monkeypatch):
    monkeypatch.setenv("VERA_BASE", "http://llm.example/v1")
    monkeypatch.setenv("VERA_MODEL", "m")
    from routers import pulse
    monkeypatch.setattr(pulse, "CHAT_TEMPLATE_KWARGS", None)
    calls = _capture(monkeypatch)
    asyncio.run(mc.complete([{"role": "user", "content": "x"}]))
    assert "chat_template_kwargs" not in calls["body"]


def test_coder_payload_carries_tools_and_budget(monkeypatch):
    monkeypatch.setenv("DREAM_BASE", "http://coder.example/v1")
    monkeypatch.setenv("DREAM_MODEL", "coder-model")
    calls = _capture(monkeypatch)
    msg = asyncio.run(mc.complete([{"role": "user", "content": "x"}], workload="coder",
                                  temperature=0.0, tools=[{"type": "function"}], max_tokens=3000))
    assert msg["content"] == "hi"
    body = calls["body"]
    assert body["model"] == "coder-model"
    assert body["tools"] == [{"type": "function"}] and body["max_tokens"] == 3000
    assert "chat_template_kwargs" not in body
    assert calls["timeout"] == 600


def test_vision_resolves_registry_only_and_appends_v1(monkeypatch):
    from routers import integrations
    monkeypatch.setattr(integrations, "integration",
                        lambda iid: {"url": "http://vision.example:8082", "model": "vlm"}
                        if iid == "vision_review" else None)
    c = mc.config("vision")
    assert c["base"] == "http://vision.example:8082/v1" and c["model"] == "vlm"
    assert c["timeout"] == 120


def test_disabled_vision_never_falls_back_to_env(monkeypatch):
    from routers import integrations
    monkeypatch.setenv("VERA_VISION_BASE", "http://vision.example:8082")
    monkeypatch.setenv("VERA_VISION_MODEL", "vlm")
    monkeypatch.setattr(integrations, "integration", lambda iid: None)
    assert not mc.configured("vision")
    assert mc.unavailable_reason("vision") is not None
