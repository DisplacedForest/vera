import json
import os
from zoneinfo import ZoneInfo

import aiohttp

from .persona import think_kwargs

OWUI_BASE = os.environ.get("OWUI_BASE", "").rstrip("/")          # Open WebUI (memory, chat promotion)
OWUI_KEY = os.environ.get("OWUI_KEY", "")
VERA_BASE = os.environ.get("VERA_BASE", "").rstrip("/")          # main LLM, any OpenAI-compatible /v1
MODEL = os.environ.get("VERA_MODEL", "")
TZ = ZoneInfo(os.environ.get("HOME_TZ", "UTC"))  # untouched cards expire the day after creation (ChatGPT-Pulse daily freshness)


def _parse_template_kwargs() -> dict | None:
    """Server-specific chat-template options (VERA_CHAT_TEMPLATE_KWARGS, JSON object —
    e.g. a hybrid-thinking toggle on llama.cpp/vLLM). Unset/invalid/empty means the
    chat request stays pure OpenAI: the field is omitted entirely."""
    raw = os.environ.get("VERA_CHAT_TEMPLATE_KWARGS", "").strip()
    if not raw:
        return None
    try:
        v = json.loads(raw)
    except ValueError:
        return None
    return v if isinstance(v, dict) and v else None


def _headers():
    return {"Authorization": f"Bearer {OWUI_KEY}", "Content-Type": "application/json"}


async def _request_json(method, url, *, timeout, **kwargs):
    async with aiohttp.ClientSession() as s:
        async with s.request(method, url, timeout=aiohttp.ClientTimeout(total=timeout), **kwargs) as r:
            return await r.json()


def _chat_payload(messages, temperature, think=None) -> dict:
    """The /chat/completions body — pure OpenAI unless template kwargs are configured.
    An explicit `think` mode ("on"/"off") resolves per-mode kwargs via persona.think_kwargs;
    no mode means the global kwargs."""
    p = {"model": MODEL, "stream": False, "temperature": temperature, "messages": messages}
    from . import pulse
    kwargs = think_kwargs(think) if think else pulse.CHAT_TEMPLATE_KWARGS
    if kwargs:
        p["chat_template_kwargs"] = kwargs
    return p


async def _vera(messages, temperature=0.4, think=None):
    d = await _request_json("POST", f"{VERA_BASE}/chat/completions",
                            timeout=300, json=_chat_payload(messages, temperature, think))
    return d["choices"][0]["message"]["content"]


async def _get_memories():
    return await _request_json("GET", f"{OWUI_BASE}/api/v1/memories/",
                               timeout=30, headers=_headers())


async def _active_users():
    """OWUI accounts — the people Vera serves. Each gets their own briefing/feed."""
    try:
        d = await _request_json("GET", f"{OWUI_BASE}/api/v1/users/", timeout=20, headers=_headers())
        users = d.get("users") if isinstance(d, dict) else d
        return [{"id": u.get("id"), "name": u.get("name")} for u in (users or []) if u.get("id")]
    except Exception:
        return []
