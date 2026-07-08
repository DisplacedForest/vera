import json
import os
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


LOOP_RULES = (
    "Loop discipline, non-negotiable:\n"
    "- Make at most one tool call per turn.\n"
    "- A result you have not been handed does not exist: never state or use a tool result "
    "before its <tool_response> arrives, and never cite a source you were not given.\n"
    "- After each result, keep a short running summary of what is established so far.\n"
    "- At the step limit, give your final answer from what is established, never from guesses."
)


def loop_budget(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


_TOOLS: dict[str, dict] = {}


def register_tool(schema: dict, handler, available=None, last_resort: bool = False) -> None:
    name = schema["function"]["name"]
    _TOOLS[name] = {"schema": schema, "handler": handler,
                    "available": available, "last_resort": last_resort}


def tool_schemas() -> list[dict]:
    entries = sorted(_TOOLS.values(), key=lambda e: e["last_resort"])
    return [e["schema"] for e in entries if e["available"] is None or e["available"]()]


async def dispatch(name: str, arguments: dict) -> str | None:
    entry = _TOOLS.get(name)
    if entry is None or (entry["available"] is not None and not entry["available"]()):
        return None
    return await entry["handler"](arguments)


def render_tools(schemas: list[dict]) -> str:
    return _INSTRUCTIONS.format(schemas=json.dumps(schemas))


def render_response(name: str, content: str) -> str:
    return f"<tool_response>{json.dumps({'name': name, 'content': content})}</tool_response>"


def parse_tool_calls(text: str) -> tuple[list[FunctionCall], list[str]]:
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
