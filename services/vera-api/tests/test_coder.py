"""Coder-agent tool transports. The live loop is exercised at deploy; everything
deterministic — protocol selection, the openai tool_calls loop, the hermes text-protocol
loop — lives here. Run under pytest."""
import asyncio
import json

from routers import coder, tool_protocol


# --------------------------------------------------------------------------- protocol selection

def test_protocol_defaults_to_openai(monkeypatch):
    monkeypatch.delenv("DREAM_TOOL_PROTOCOL", raising=False)
    assert coder.tool_protocol() == "openai"


def test_protocol_hermes_opt_in(monkeypatch):
    monkeypatch.setenv("DREAM_TOOL_PROTOCOL", "HERMES")
    assert coder.tool_protocol() == "hermes"
    monkeypatch.setenv("DREAM_TOOL_PROTOCOL", "anything-else")
    assert coder.tool_protocol() == "openai"


def test_protocol_mlx_is_deprecated_alias_for_hermes(monkeypatch):
    monkeypatch.setenv("DREAM_TOOL_PROTOCOL", "mlx")
    assert coder.tool_protocol() == "hermes"


# --------------------------------------------------------------------------- loop harness

def _call(query_args, call_id="call_1", name="web_search"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": query_args}}


def _agent(monkeypatch, script, protocol=None):
    """Run chat_agent against a scripted _llm; returns (answer, requests) where each request
    is the (messages, tools) pair the fake server saw."""
    requests = []
    replies = list(script)

    async def fake_llm(messages, temperature, tools=None):
        requests.append(([dict(m) for m in messages], tools))
        return replies.pop(0)

    async def fake_search(query):
        return f"results for <{query}>"

    monkeypatch.setattr(coder, "_llm", fake_llm)
    monkeypatch.setattr(coder, "_run_search", fake_search)
    if protocol is None:
        monkeypatch.delenv("DREAM_TOOL_PROTOCOL", raising=False)
    else:
        monkeypatch.setenv("DREAM_TOOL_PROTOCOL", protocol)
    answer = asyncio.run(coder.chat_agent("sys prompt", "user prompt"))
    return answer, requests


# --------------------------------------------------------------------------- openai strategy

def test_openai_no_tool_is_final_answer(monkeypatch):
    answer, requests = _agent(monkeypatch, [{"content": "done."}])
    assert answer == "done."
    assert len(requests) == 1
    messages, tools = requests[0]
    assert tools == coder.TOOLS                       # the request advertises the tool
    assert messages[0] == {"role": "system", "content": "sys prompt"}  # no protocol text injected


def test_openai_single_call_round_trip(monkeypatch):
    answer, requests = _agent(monkeypatch, [
        {"content": None, "tool_calls": [_call(json.dumps({"query": "alpha"}))]},
        {"content": "answer from results."},
    ])
    assert answer == "answer from results."
    messages, _ = requests[1]
    assert messages[-2]["role"] == "assistant" and messages[-2]["tool_calls"]
    tool_msg = messages[-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "call_1"
    assert "results for <alpha>" in tool_msg["content"]


def test_openai_multi_step(monkeypatch):
    answer, requests = _agent(monkeypatch, [
        {"content": None, "tool_calls": [_call(json.dumps({"query": "one"}), "c1")]},
        {"content": None, "tool_calls": [_call(json.dumps({"query": "two"}), "c2")]},
        {"content": "final."},
    ])
    assert answer == "final."
    tool_msgs = [m for m, _ in [requests[2]] for m in m if m["role"] == "tool"]
    assert [t["tool_call_id"] for t in tool_msgs] == ["c1", "c2"]


def test_openai_malformed_arguments_never_crash(monkeypatch):
    answer, requests = _agent(monkeypatch, [
        {"content": None, "tool_calls": [_call("{not json")]},
        {"content": "recovered."},
    ])
    assert answer == "recovered."
    tool_msg = requests[1][0][-1]
    assert tool_msg["role"] == "tool" and "malformed tool arguments" in tool_msg["content"]


def test_openai_unknown_tool_name(monkeypatch):
    answer, requests = _agent(monkeypatch, [
        {"content": None, "tool_calls": [_call(json.dumps({"query": "x"}), name="frobnicate")]},
        {"content": "ok."},
    ])
    assert answer == "ok."
    assert "unknown tool" in requests[1][0][-1]["content"]


def test_openai_out_of_steps_forces_final_without_tools(monkeypatch):
    looping = {"content": None, "tool_calls": [_call(json.dumps({"query": "again"}))]}
    answer, requests = _agent(monkeypatch, [looping, looping, looping, {"content": "forced."}])
    assert answer == "forced."
    final_messages, final_tools = requests[-1]
    assert final_tools is None                         # no tools advertised on the forced call
    assert "final answer now" in final_messages[-1]["content"]


# --------------------------------------------------------------------------- hermes strategy

def _hermes_call(query):
    return f'<tool_call>\n{{"arguments": {{"query": "{query}"}}, "name": "web_search"}}\n</tool_call>'


def test_hermes_loop_uses_text_protocol(monkeypatch):
    answer, requests = _agent(monkeypatch, [
        {"content": _hermes_call("beta")},
        {"content": "text-protocol answer."},
    ], protocol="hermes")
    assert answer == "text-protocol answer."
    first_messages, first_tools = requests[0]
    assert first_tools is None                         # hermes requests never advertise tools
    assert first_messages[0]["content"].startswith(tool_protocol.render_tools(coder.TOOLS))
    assert first_messages[0]["content"].endswith("sys prompt")
    follow = requests[1][0][-1]
    assert follow["role"] == "user" and "<tool_response>" in follow["content"]
    assert "results for <beta>" in follow["content"]


def test_hermes_plain_reply_is_final(monkeypatch):
    answer, requests = _agent(monkeypatch, [{"content": "no tool needed."}], protocol="hermes")
    assert answer == "no tool needed."
    assert len(requests) == 1


def test_hermes_legacy_mlx_value_runs_the_hermes_loop(monkeypatch):
    answer, requests = _agent(monkeypatch, [
        {"content": _hermes_call("gamma")},
        {"content": "done."},
    ], protocol="mlx")
    assert answer == "done."
    assert "results for <gamma>" in requests[1][0][-1]["content"]


def test_hermes_unknown_tool_gets_corrective_response(monkeypatch):
    answer, requests = _agent(monkeypatch, [
        {"content": '<tool_call>{"arguments": {}, "name": "frobnicate"}</tool_call>'},
        {"content": "recovered."},
    ], protocol="hermes")
    assert answer == "recovered."
    follow = requests[1][0][-1]["content"]
    assert "unknown tool" in follow and "<tool_response>" in follow


def test_hermes_malformed_call_gets_corrective_response(monkeypatch):
    answer, requests = _agent(monkeypatch, [
        {"content": '<tool_call>{"name": "web_search"}</tool_call>'},
        {"content": "recovered."},
    ], protocol="hermes")
    assert answer == "recovered."
    assert "invalid tool_call" in requests[1][0][-1]["content"]


def test_hermes_multiple_calls_all_answered(monkeypatch):
    answer, requests = _agent(monkeypatch, [
        {"content": _hermes_call("one") + "\n" + _hermes_call("two")},
        {"content": "combined."},
    ], protocol="hermes")
    assert answer == "combined."
    follow = requests[1][0][-1]["content"]
    assert "results for <one>" in follow and "results for <two>" in follow


def test_hermes_out_of_steps_forces_final(monkeypatch):
    looping = {"content": _hermes_call("again")}
    answer, requests = _agent(monkeypatch, [looping, looping, looping, {"content": "forced."}], protocol="hermes")
    assert answer == "forced."
    assert "final answer now" in requests[-1][0][-1]["content"]


# --------------------------------------------------------------------------- registry resolution

def test_registry_coder_entry_resolves_endpoint_and_protocol(monkeypatch, tmp_path):
    from routers import integrations, integrations_store
    monkeypatch.setattr(integrations_store, "PATH", str(tmp_path / "integrations.json"), raising=False)
    monkeypatch.setenv("DREAM_BASE", "http://coder.example/v1")
    monkeypatch.setenv("DREAM_MODEL", "coder-model")
    monkeypatch.delenv("DREAM_TOOL_PROTOCOL", raising=False)
    # the optional protocol field doesn't block configured/enabled
    r = integrations._resolved("coder")
    assert r["configured"] and r["enabled"]
    assert coder._endpoint() == ("http://coder.example/v1", "coder-model")
    assert coder.tool_protocol() == "openai"
    monkeypatch.setenv("DREAM_TOOL_PROTOCOL", "hermes")
    assert coder.tool_protocol() == "hermes"


def test_env_fallback_when_registry_unconfigured(monkeypatch):
    monkeypatch.delenv("DREAM_BASE", raising=False)
    monkeypatch.delenv("DREAM_MODEL", raising=False)
    monkeypatch.setenv("DREAM_TOOL_PROTOCOL", "hermes")
    # no registry entry resolves -> the DREAM_* env (module fallbacks) still decides
    assert coder.tool_protocol() == "hermes"
