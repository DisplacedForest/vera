"""Endpoint-protocol tests for the primary chat call and image generation — request and
response shapes are pure functions; protocol resolution mirrors the coder's registry
pattern (env seeds the integration entry, the field is interpreted in one place).
Run: python3 -m pytest tests/test_pulse_protocols.py
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from routers import integrations_store as ist  # noqa: E402
from routers import pulse  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setattr(ist, "PATH", str(tmp_path / "integrations.json"))
    for n in ("IMAGE_PROTOCOL", "VERA_IMAGE_BASE", "VERA_CHAT_TEMPLATE_KWARGS"):
        monkeypatch.delenv(n, raising=False)
    yield


# --- primary chat call: pure OpenAI unless template kwargs are configured ----------------

def test_chat_payload_is_pure_openai_by_default(monkeypatch):
    monkeypatch.setattr(pulse, "CHAT_TEMPLATE_KWARGS", None)
    p = pulse._chat_payload([{"role": "user", "content": "hi"}], 0.4)
    assert set(p) == {"model", "stream", "temperature", "messages"}
    assert "chat_template_kwargs" not in p


def test_chat_payload_carries_configured_kwargs(monkeypatch):
    monkeypatch.setattr(pulse, "CHAT_TEMPLATE_KWARGS", {"enable_thinking": False})
    p = pulse._chat_payload([], 0.1)
    assert p["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.parametrize("raw,expected", [
    ("", None),
    ('{"enable_thinking": false}', {"enable_thinking": False}),
    ("{}", None),                 # empty object -> omit the field
    ("not json", None),           # invalid -> pure OpenAI, never a crash
    ('["a"]', None),              # non-object -> omit
])
def test_template_kwargs_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("VERA_CHAT_TEMPLATE_KWARGS", raw)
    assert pulse._parse_template_kwargs() == expected


# --- image protocol resolution: registry-pinned by env, openai unless explicitly vera ----

def test_protocol_defaults_openai_and_env_selects_vera(monkeypatch):
    monkeypatch.setenv("VERA_IMAGE_BASE", "http://img.example")
    assert pulse.image_protocol() == "openai"
    monkeypatch.setenv("IMAGE_PROTOCOL", "vera")
    assert pulse.image_protocol() == "vera"
    monkeypatch.setenv("IMAGE_PROTOCOL", "nonsense")
    assert pulse.image_protocol() == "openai"   # anything not 'vera' is the standard


def test_image_base_resolves_through_registry(monkeypatch):
    monkeypatch.setenv("VERA_IMAGE_BASE", "http://img.example/")
    assert pulse._image_base() == "http://img.example"


# --- request/response shapes per protocol ------------------------------------------------

def test_openai_image_request_shape():
    path, payload = pulse._image_request("a quiet harbor at dawn", "soft gouache", 3, "openai")
    assert path == "/v1/images/generations"
    assert payload == {"prompt": "a quiet harbor at dawn. Art style: soft gouache.",
                       "size": "1024x768", "n": 1, "response_format": "b64_json"}
    # determinism knobs are a vera-protocol feature — never leak into the standard call
    assert "seed" not in payload and "steps" not in payload and "style" not in payload


def test_openai_image_request_without_style():
    _, payload = pulse._image_request("a quiet harbor", "", 0, "openai")
    assert payload["prompt"] == "a quiet harbor"


def test_vera_image_request_is_byte_for_byte():
    path, payload = pulse._image_request("a quiet harbor", "soft gouache", 3, "vera")
    assert path == "/generate"
    assert payload == {"prompt": "a quiet harbor", "style": "soft gouache",
                       "width": 1024, "height": 768, "steps": 20, "seed": 1003}


def test_image_response_parsing_both_protocols():
    assert pulse._image_b64({"data": [{"b64_json": "abc"}]}, "openai") == "abc"
    assert pulse._image_b64({"data": []}, "openai") is None
    assert pulse._image_b64({}, "openai") is None
    assert pulse._image_b64({"image_base64": "xyz"}, "vera") == "xyz"
    assert pulse._image_b64({}, "vera") is None


def test_vision_arbitration_noops_outside_vera_protocol(monkeypatch):
    """In openai mode the pause/resume hook must return without any network attempt —
    an unroutable base proves no call is made (it would exceed the timeout otherwise)."""
    monkeypatch.setenv("VERA_IMAGE_BASE", "http://192.0.2.1:1")
    asyncio.run(asyncio.wait_for(pulse._vision(pause=True), timeout=0.5))
