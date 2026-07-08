"""The Hermes tool-calling contract — Vera's model-agnostic text protocol for tool use.

Wire format, plaintext any GGUF/MLX model can emit and any OpenAI-compatible server can
pass through verbatim:

  * Tools are advertised in the system prompt as one JSON array of OpenAI-style function
    schemas inside <tools>...</tools> (render_tools).
  * The model calls a tool by emitting <tool_call>{"arguments": {...}, "name": "..."}</tool_call>.
  * Each result is returned to the model as <tool_response>{"name": ..., "content": ...}</tool_response>
    (render_response).

Every call is validated against the FunctionCall schema before dispatch — deterministic
code, never LLM judgment. A malformed block yields a corrective reason string so the
caller can answer the model with a fix-it <tool_response> instead of raising.
"""
import json
import re

from pydantic import BaseModel, ValidationError


class FunctionCall(BaseModel):
    name: str
    arguments: dict


_TOOL_CALL = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)

_INSTRUCTIONS = (
    "You may call functions to help with the task. The available functions are listed "
    "below as JSON schemas:\n\n<tools>{schemas}</tools>\n\n"
    "To call a function, respond with a JSON object (arguments first, then name) inside "
    "<tool_call></tool_call> tags:\n"
    '<tool_call>\n{{"arguments": <args-object>, "name": "<function-name>"}}\n</tool_call>\n'
    "Each result arrives inside <tool_response></tool_response> tags. Never state or use a "
    "result you have not received. When no function is needed, answer directly."
)


def render_tools(schemas: list[dict]) -> str:
    """The system-prompt preamble advertising `schemas` (OpenAI-style function schemas)
    and documenting the call format."""
    return _INSTRUCTIONS.format(schemas=json.dumps(schemas))


def render_response(name: str, content: str) -> str:
    """One tool result, wrapped for the model."""
    return f"<tool_response>{json.dumps({'name': name, 'content': content})}</tool_response>"


def parse_tool_calls(text: str) -> tuple[list[FunctionCall], list[str]]:
    """Every <tool_call> block in `text`, validated. Returns (calls, errors): well-formed
    blocks become FunctionCall objects in order of appearance; each malformed block becomes
    a reason string. Never raises."""
    calls: list[FunctionCall] = []
    errors: list[str] = []
    for m in _TOOL_CALL.finditer(text or ""):
        raw = m.group(1)
        try:
            payload = json.loads(raw)
        except ValueError:
            errors.append("tool_call is not valid JSON")
            continue
        try:
            calls.append(FunctionCall.model_validate(payload))
        except ValidationError as e:
            first = e.errors()[0]
            where = ".".join(str(p) for p in first.get("loc") or ()) or "payload"
            errors.append(f"invalid tool_call ({where}: {first.get('msg', 'invalid')})")
    return calls, errors
