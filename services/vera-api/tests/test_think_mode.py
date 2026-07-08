"""Per-surface reasoning mode threading — _chat_payload must carry the resolved
chat-template kwargs for an explicit think mode and stay pure OpenAI when nothing
is configured. Run: python3 -m pytest tests/test_think_mode.py
"""
import pytest

from routers import pulse


@pytest.fixture
def clear_think_env(monkeypatch):
    for name in ("VERA_THINK_KWARGS_ON", "VERA_THINK_KWARGS_OFF", "VERA_CHAT_TEMPLATE_KWARGS"):
        monkeypatch.delenv(name, raising=False)


MSGS = [{"role": "user", "content": "hi"}]


def test_no_think_no_global_stays_pure_openai(clear_think_env, monkeypatch):
    monkeypatch.setattr(pulse, "CHAT_TEMPLATE_KWARGS", None)
    p = pulse._chat_payload(MSGS, 0.4)
    assert "chat_template_kwargs" not in p
    assert p["messages"] == MSGS and p["temperature"] == 0.4


def test_no_think_keeps_global_kwargs(clear_think_env, monkeypatch):
    monkeypatch.setattr(pulse, "CHAT_TEMPLATE_KWARGS", {"enable_thinking": False})
    p = pulse._chat_payload(MSGS, 0.4)
    assert p["chat_template_kwargs"] == {"enable_thinking": False}


def test_think_mode_resolves_per_mode_config(clear_think_env, monkeypatch):
    monkeypatch.setattr(pulse, "CHAT_TEMPLATE_KWARGS", None)
    monkeypatch.setenv("VERA_THINK_KWARGS_OFF", '{"enable_thinking": false}')
    monkeypatch.setenv("VERA_THINK_KWARGS_ON", '{"enable_thinking": true}')
    assert pulse._chat_payload(MSGS, 0.4, think="off")["chat_template_kwargs"] == {"enable_thinking": False}
    assert pulse._chat_payload(MSGS, 0.4, think="on")["chat_template_kwargs"] == {"enable_thinking": True}


def test_think_mode_unconfigured_omits_field(clear_think_env, monkeypatch):
    monkeypatch.setattr(pulse, "CHAT_TEMPLATE_KWARGS", None)
    assert "chat_template_kwargs" not in pulse._chat_payload(MSGS, 0.4, think="on")
    assert "chat_template_kwargs" not in pulse._chat_payload(MSGS, 0.4, think="off")


def test_think_mode_falls_back_to_global_env(clear_think_env, monkeypatch):
    monkeypatch.setattr(pulse, "CHAT_TEMPLATE_KWARGS", None)
    monkeypatch.setenv("VERA_CHAT_TEMPLATE_KWARGS", '{"enable_thinking": false}')
    assert pulse._chat_payload(MSGS, 0.4, think="on")["chat_template_kwargs"] == {"enable_thinking": False}
