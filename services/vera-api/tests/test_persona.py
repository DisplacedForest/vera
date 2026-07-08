"""Persona identity/templating tests — the identity load path must never raise (every
prose card flows through voiced()), and shipped text must leave no {owner}/{location}
placeholders behind. Run: python3 -m pytest tests/test_persona.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routers"))
import persona  # noqa: E402


def test_vera_identity_loads_and_is_personalized():
    persona.vera_identity.cache_clear()
    text = persona.vera_identity()
    assert text.strip()
    assert "{owner}" not in text and "{location}" not in text


def test_vera_identity_fallback_when_soul_missing(monkeypatch):
    monkeypatch.setattr(persona, "SOUL_PATH", "/nonexistent/SOUL.md")
    persona.vera_identity.cache_clear()
    text = persona.vera_identity()
    assert text.strip()
    assert "{owner}" not in text
    persona.vera_identity.cache_clear()


def test_voiced_carries_identity_and_task():
    out = persona.voiced("Summarize the kitchen.")
    assert out.endswith("Summarize the kitchen.")
    assert len(out) > len("Summarize the kitchen.")


def test_personalize_defaults_when_unconfigured(monkeypatch):
    monkeypatch.setattr(persona, "_OWNER_NAME", "")
    monkeypatch.setattr(persona, "_LOCATION_NAME", "")
    out = persona.personalize("{owner} lives near {location}.")
    assert out == "the owner lives near the home area."


def test_personalize_uses_configured_values(monkeypatch):
    monkeypatch.setattr(persona, "_OWNER_NAME", "Alex")
    monkeypatch.setattr(persona, "_LOCATION_NAME", "Springfield, IL")
    out = persona.personalize("{owner} / {location}")
    assert out == "Alex / Springfield, IL"


def _clear_think_env(monkeypatch):
    for name in ("VERA_THINK_KWARGS_ON", "VERA_THINK_KWARGS_OFF", "VERA_CHAT_TEMPLATE_KWARGS"):
        monkeypatch.delenv(name, raising=False)


def test_think_kwargs_returns_configured_mode_objects(monkeypatch):
    _clear_think_env(monkeypatch)
    monkeypatch.setenv("VERA_THINK_KWARGS_ON", '{"enable_thinking": true}')
    monkeypatch.setenv("VERA_THINK_KWARGS_OFF", '{"enable_thinking": false}')
    assert persona.think_kwargs("on") == {"enable_thinking": True}
    assert persona.think_kwargs("off") == {"enable_thinking": False}


def test_think_kwargs_falls_back_to_global(monkeypatch):
    _clear_think_env(monkeypatch)
    monkeypatch.setenv("VERA_CHAT_TEMPLATE_KWARGS", '{"enable_thinking": false}')
    assert persona.think_kwargs("on") == {"enable_thinking": False}
    assert persona.think_kwargs("off") == {"enable_thinking": False}


def test_think_kwargs_unset_returns_none(monkeypatch):
    _clear_think_env(monkeypatch)
    assert persona.think_kwargs("on") is None
    assert persona.think_kwargs("off") is None


def test_think_kwargs_invalid_json_uses_fallback(monkeypatch):
    _clear_think_env(monkeypatch)
    monkeypatch.setenv("VERA_THINK_KWARGS_ON", "{not json")
    monkeypatch.setenv("VERA_CHAT_TEMPLATE_KWARGS", '{"enable_thinking": true}')
    assert persona.think_kwargs("on") == {"enable_thinking": True}


def test_think_kwargs_non_object_json_uses_fallback(monkeypatch):
    _clear_think_env(monkeypatch)
    monkeypatch.setenv("VERA_THINK_KWARGS_OFF", '["not", "an", "object"]')
    assert persona.think_kwargs("off") is None
    monkeypatch.setenv("VERA_THINK_KWARGS_OFF", "{}")
    assert persona.think_kwargs("off") is None
