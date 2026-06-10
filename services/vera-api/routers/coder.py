"""Agentic tool-use for the dream/coder endpoint (any OpenAI-compatible /v1).

Two tool-call transports, selected by DREAM_TOOL_PROTOCOL:

  openai (default) — standard OpenAI tool calling: the request advertises `tools`, the model
      returns `tool_calls`, and results go back as `role: "tool"` messages. Works with any
      server that implements the spec.

  mlx — a text-protocol fallback for servers that run tool-capable models but do not convert
      the model's native tool-call syntax into OpenAI `tool_calls`: the call format is
      documented in a system instruction and the reply text is parsed for it:

          <function=web_search><parameter=query>...</parameter></function>

Either way the loop is ours: execute the search through our SearXNG backend, feed results
back, and continue until the model answers without a tool call (or the step budget forces a
final answer). `web_search` is the single tool, expressed per-protocol. Everything runs on
the coder endpoint — the integrations registry's 'coder' entry (env-seeded by DREAM_BASE /
DREAM_MODEL / DREAM_TOOL_PROTOCOL, editable in the plugin store); the primary model server
is never touched.
"""
import json
import os
import re

import aiohttp

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
    """The active tool-call transport: 'openai' unless the coder integration's tool_protocol
    field (pinned by DREAM_TOOL_PROTOCOL when set in env) says 'mlx'. Read at call time —
    the one place the flag is interpreted."""
    raw = _registry_values().get("tool_protocol") or os.environ.get("DREAM_TOOL_PROTOCOL", "")
    return "mlx" if raw.strip().lower() == "mlx" else "openai"


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


# ------------------------------------------------------------------ mlx text-protocol pieces

TOOL_SYS = (
    "You have ONE tool, web_search, for looking up current facts. When you need to check something, "
    "respond with EXACTLY this and nothing else:\n"
    "<function=web_search><parameter=query>your search query</parameter></function>\n"
    "You will then be given the results and may search again or give your final answer. Prefer "
    "searching over relying on memory whenever a fact could be recent or uncertain."
)

_FN = re.compile(r"<function=web_search>(.*?)</function>", re.DOTALL)
_QUERY = re.compile(r"<parameter=query>\s*(.*?)\s*</parameter>", re.DOTALL)


def parse_search_call(text):
    """Extract the web_search query from the model's text-protocol tool call, or None if it isn't one."""
    m = _FN.search(text or "")
    if not m:
        return None
    q = _QUERY.search(m.group(1))
    return q.group(1).strip() if q else None


# ------------------------------------------------------------------ shared plumbing

async def _llm(messages, temperature, tools=None):
    """One chat-completions call; returns the full response message object (content and, when
    the server supports the openai protocol, any tool_calls)."""
    base, model = _endpoint()
    body = {"model": model, "stream": False, "temperature": temperature, "messages": messages}
    if tools:
        body["tools"] = tools
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
    if tool_protocol() == "mlx":
        return await _chat_agent_mlx(system, user, max_steps, temperature)
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


async def _chat_agent_mlx(system, user, max_steps, temperature):
    """Text-protocol loop: the tool format is prepended to the system prompt and each reply is
    parsed for a call; a reply that isn't a tool call is the final answer."""
    messages = [
        {"role": "system", "content": f"{TOOL_SYS}\n\n{system}"},
        {"role": "user", "content": user},
    ]
    for _ in range(max_steps):
        text = (await _llm(messages, temperature)).get("content") or ""
        query = parse_search_call(text)
        if not query:
            return text  # final answer
        results = await _run_search(query)
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content":
                         f'web_search results for "{query}":\n{results}\n\n'
                         "Search again if you still need to, or give your final answer now."})
    # out of steps — force a final answer with no more tools
    messages.append({"role": "user", "content": "Give your final answer now — do not call the tool again."})
    return (await _llm(messages, temperature)).get("content") or ""
