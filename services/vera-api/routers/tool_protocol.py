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
