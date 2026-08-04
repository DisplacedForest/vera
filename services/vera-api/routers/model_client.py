import os

import aiohttp


class ModelUnavailable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


WORKLOADS: dict[str, dict] = {
    "main": {"integration": "model", "env_base": "VERA_BASE", "env_model": "VERA_MODEL",
             "timeout": 300,
             "reason": "no model is configured. Set VERA_BASE and VERA_MODEL."},
    "coder": {"integration": "coder", "env_base": "DREAM_BASE", "env_model": "DREAM_MODEL",
              "timeout": 600,
              "reason": "no coder model is configured. Set DREAM_BASE and DREAM_MODEL."},
    "vision": {"integration": "vision_review", "env_base": None, "env_model": None,
               "timeout": 120,
               "reason": "no vision model is configured. Set VERA_VISION_BASE and VERA_VISION_MODEL."},
}


def _registry_values(iid: str) -> dict:
    try:
        from . import integrations
        return integrations.integration(iid) or {}
    except Exception:
        return {}


def config(workload: str) -> dict:
    w = WORKLOADS[workload]
    v = _registry_values(w["integration"])
    env_base = os.environ.get(w["env_base"], "") if w["env_base"] else ""
    env_model = os.environ.get(w["env_model"], "") if w["env_model"] else ""
    base = (v.get("url") or env_base).strip().rstrip("/")
    model = (v.get("model") or env_model).strip()
    if workload == "vision" and base and not base.endswith("/v1"):
        base = f"{base}/v1"
    return {"base": base, "model": model, "timeout": w["timeout"]}


def configured(workload: str) -> bool:
    c = config(workload)
    return bool(c["base"] and c["model"])


def unavailable_reason(workload: str) -> str | None:
    return None if configured(workload) else WORKLOADS[workload]["reason"]


def _main_kwargs(think: str | None) -> dict | None:
    from . import pulse
    from .persona import think_kwargs
    return think_kwargs(think) if think else pulse.CHAT_TEMPLATE_KWARGS


async def _post(url: str, body: dict, timeout: int) -> dict:
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=body, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            return await r.json()


async def complete(messages, *, workload: str = "main", temperature: float = 0.4,
                   think: str | None = None, tools=None, max_tokens: int | None = None,
                   timeout: int | None = None) -> dict:
    reason = unavailable_reason(workload)
    if reason:
        raise ModelUnavailable(reason)
    c = config(workload)
    body = {"model": c["model"], "stream": False, "temperature": temperature, "messages": messages}
    if workload == "main":
        kwargs = _main_kwargs(think)
        if kwargs:
            body["chat_template_kwargs"] = kwargs
    if tools:
        body["tools"] = tools
    if max_tokens:
        body["max_tokens"] = max_tokens
    d = await _post(f"{c['base']}/chat/completions", body, timeout or c["timeout"])
    return d["choices"][0]["message"]


async def complete_text(messages, **kw) -> str:
    return (await complete(messages, **kw))["content"]
