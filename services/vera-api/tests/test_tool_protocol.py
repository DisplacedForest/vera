"""Hermes tool-calling contract — parsing, validation, and rendering are deterministic
code; every malformed block becomes a corrective reason, never an exception. Run under
pytest."""
import json

from routers import tool_protocol as tp


SCHEMAS = [{
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Look up current facts on the web.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}]


def _block(payload):
    return f"<tool_call>\n{payload}\n</tool_call>"


# --------------------------------------------------------------------------- parsing

def test_valid_call_parses():
    calls, errors = tp.parse_tool_calls(_block('{"arguments": {"query": "alpha"}, "name": "web_search"}'))
    assert errors == []
    assert len(calls) == 1
    assert calls[0].name == "web_search"
    assert calls[0].arguments == {"query": "alpha"}


def test_reversed_key_order_parses():
    calls, errors = tp.parse_tool_calls(_block('{"name": "web_search", "arguments": {"query": "beta"}}'))
    assert errors == []
    assert calls[0].name == "web_search" and calls[0].arguments == {"query": "beta"}


def test_surrounding_prose_is_tolerated():
    text = ("Let me look that up.\n"
            + _block('{"arguments": {"query": "gamma"}, "name": "web_search"}')
            + "\nI will report back.")
    calls, errors = tp.parse_tool_calls(text)
    assert errors == []
    assert calls[0].arguments == {"query": "gamma"}


def test_multiple_blocks_all_parse_in_order():
    text = (_block('{"arguments": {"query": "one"}, "name": "web_search"}')
            + "\n"
            + _block('{"arguments": {"query": "two"}, "name": "web_search"}'))
    calls, errors = tp.parse_tool_calls(text)
    assert errors == []
    assert [c.arguments["query"] for c in calls] == ["one", "two"]


def test_no_blocks_returns_empty():
    assert tp.parse_tool_calls("Here is my final answer.") == ([], [])
    assert tp.parse_tool_calls("") == ([], [])


# --------------------------------------------------------------------------- rejection

def test_malformed_json_rejects_deterministically():
    calls, errors = tp.parse_tool_calls(_block('{"arguments": {"query": '))
    assert calls == []
    assert len(errors) == 1


def test_missing_name_rejects():
    calls, errors = tp.parse_tool_calls(_block('{"arguments": {"query": "x"}}'))
    assert calls == [] and len(errors) == 1


def test_missing_arguments_rejects():
    calls, errors = tp.parse_tool_calls(_block('{"name": "web_search"}'))
    assert calls == [] and len(errors) == 1


def test_non_object_arguments_rejects():
    calls, errors = tp.parse_tool_calls(_block('{"arguments": "query=x", "name": "web_search"}'))
    assert calls == [] and len(errors) == 1


def test_non_dict_json_rejects():
    calls, errors = tp.parse_tool_calls(_block('["web_search", {"query": "x"}]'))
    assert calls == [] and len(errors) == 1


def test_mixed_valid_and_malformed_blocks():
    text = (_block('{"arguments": {"query": "good"}, "name": "web_search"}')
            + _block('{"name": "web_search"}'))
    calls, errors = tp.parse_tool_calls(text)
    assert len(calls) == 1 and calls[0].arguments == {"query": "good"}
    assert len(errors) == 1


# --------------------------------------------------------------------------- rendering

def test_render_tools_embeds_schemas_verbatim():
    out = tp.render_tools(SCHEMAS)
    start, end = out.index("<tools>") + len("<tools>"), out.index("</tools>")
    assert json.loads(out[start:end]) == SCHEMAS
    assert "<tool_call>" in out and "</tool_call>" in out


def test_render_response_is_valid_json_with_both_fields():
    out = tp.render_response("web_search", "results here")
    start, end = out.index("<tool_response>") + len("<tool_response>"), out.index("</tool_response>")
    payload = json.loads(out[start:end])
    assert payload == {"name": "web_search", "content": "results here"}
