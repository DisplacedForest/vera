import json
import os

import aiohttp

from .tool_protocol import parse_tool_calls, render_response, render_tools
from .websearch import SearchRequest, search as web_search

CODER_BASE = os.environ.get("DREAM_BASE", "").rstrip("/")  # coder LLM, any OpenAI-compatible /v1
CODER_MODEL = os.environ.get("DREAM_MODEL", "")


def _registry_values() -> dict:
    """The integrations registry's 'coder' entry when it resolves there (env pins fields,
    the store carries plugin-store edits — changes apply without a restart); empty when the
    registry is unavailable or the entry is unconfigured."""
    try:
        from . import integrations
        return integrations.integration("coder") or {}
    except Exception:
        return {}


def _endpoint() -> tuple[str, str]:
    """The coder endpoint (base, model) — registry value, else the DREAM_* env directly."""
    v = _registry_values()
    return (v.get("url") or CODER_BASE).rstrip("/"), v.get("model") or CODER_MODEL


def tool_protocol() -> str:
    raw = (_registry_values().get("tool_protocol") or os.environ.get("DREAM_TOOL_PROTOCOL", "")).strip().lower()
    return "hermes" if raw == "hermes" else "openai"


# ------------------------------------------------------------------ openai protocol pieces

TOOLS = [{
    "type": "function",
    "function": {
        "name": "web_search",
        "description": ("Look up current facts on the web. Prefer searching over relying on "
                        "memory whenever a fact could be recent or uncertain."),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "the search query"}},
            "required": ["query"],
        },
    },
}]


# ------------------------------------------------------------------ shared plumbing

async def _llm(messages, temperature, tools=None, max_tokens=None):
    """One chat-completions call; returns the full response message object (content and, when
    the server supports the openai protocol, any tool_calls). `max_tokens` is sent only when
    given — some servers default to a small generation cap, so callers expecting a long
    structured reply must set their own budget."""
    base, model = _endpoint()
    body = {"model": model, "stream": False, "temperature": temperature, "messages": messages}
    if tools:
        body["tools"] = tools
    if max_tokens:
        body["max_tokens"] = max_tokens
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"{base}/chat/completions",
            json=body,
            timeout=aiohttp.ClientTimeout(total=600),
        ) as r:
            d = await r.json()
    return d["choices"][0]["message"]


async def _run_search(query):
    try:
        res = await web_search(SearchRequest(query=query, fetch_pages=2, max_results=5))
        out = "\n\n".join(
            f"[{i + 1}] {getattr(x, 'title', '') or getattr(x, 'url', '')}\n{(getattr(x, 'content', '') or '')[:500]}"
            for i, x in enumerate(res.results) if getattr(x, "url", None))
        return out or "(no results)"
    except Exception as e:
        return f"(search error: {e})"


# ------------------------------------------------------------------ the agent loop

async def chat_agent(system, user, max_steps=3, temperature=0.2):
    """Run the coder with the web_search tool until it answers (or max_steps), speaking the
    configured tool protocol. `system` carries the caller's identity + task instructions.
    Returns the final text."""
    if tool_protocol() == "hermes":
        return await _chat_agent_hermes(system, user, max_steps, temperature)
    return await _chat_agent_openai(system, user, max_steps, temperature)


async def _tool_result(call):
    """Execute one openai-protocol tool call, tolerantly: malformed JSON arguments or an
    unknown tool name produce a corrective tool message, never an exception."""
    fn = call.get("function") or {}
    if fn.get("name") != "web_search":
        return f"(unknown tool '{fn.get('name')}' — only web_search exists)"
    try:
        args = json.loads(fn.get("arguments") or "{}")
        query = (args.get("query") or "").strip() if isinstance(args, dict) else ""
    except (json.JSONDecodeError, TypeError):
        query = ""
    if not query:
        return ('(malformed tool arguments — call web_search with JSON arguments '
                '{"query": "..."} or give your final answer)')
    return f'web_search results for "{query}":\n{await _run_search(query)}'


async def _chat_agent_openai(system, user, max_steps, temperature):
    """Standard tool calling. A reply without tool_calls is the final answer; the out-of-steps
    fallback re-asks without advertising tools so a compliant server cannot call again."""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    for _ in range(max_steps):
        msg = await _llm(messages, temperature, tools=TOOLS)
        calls = msg.get("tool_calls") or []
        if not calls:
            return msg.get("content") or ""
        messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": calls})
        for call in calls:
            messages.append({"role": "tool", "tool_call_id": call.get("id") or "",
                             "content": await _tool_result(call)})
    # out of steps — force a final answer with no more tools
    messages.append({"role": "user", "content": "Give your final answer now — do not call the tool again."})
    return (await _llm(messages, temperature)).get("content") or ""


async def _hermes_response(call):
    if call.name != "web_search":
        return render_response(call.name, f"unknown tool '{call.name}' — only web_search exists")
    query = str(call.arguments.get("query") or "").strip()
    if not query:
        return render_response("web_search", ('malformed arguments — call web_search with '
                                              '{"query": "..."} or give your final answer'))
    return render_response("web_search", await _run_search(query))


async def _chat_agent_hermes(system, user, max_steps, temperature):
    messages = [
        {"role": "system", "content": f"{render_tools(TOOLS)}\n\n{system}"},
        {"role": "user", "content": user},
    ]
    for _ in range(max_steps):
        text = (await _llm(messages, temperature)).get("content") or ""
        calls, errors = parse_tool_calls(text)
        if not calls and not errors:
            return text
        responses = [await _hermes_response(c) for c in calls]
        responses += [render_response("error", f'{reason}; emit {{"arguments": {{...}}, "name": "..."}}')
                      for reason in errors]
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content":
                         "\n".join(responses) + "\n\n"
                         "Call another tool if you still need to, or give your final answer now."})
    messages.append({"role": "user", "content": "Give your final answer now — do not call the tool again."})
    return (await _llm(messages, temperature)).get("content") or ""
