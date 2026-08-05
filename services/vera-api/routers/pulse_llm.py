import json
import os
from zoneinfo import ZoneInfo

import aiohttp

from .persona import think_kwargs

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
    from . import model_client
    return await model_client.complete_text(messages, temperature=temperature, think=think)


async def _get_memories(user_id=None):
    from . import memory_context
    return await memory_context.native_memories(user_id)


async def _active_users():
    from .identity import active_users
    return await active_users()
