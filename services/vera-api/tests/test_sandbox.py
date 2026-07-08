import asyncio

from routers import sandbox, tool_protocol


def test_unconfigured_exec_returns_disabled(monkeypatch):
    monkeypatch.delenv("VERA_SANDBOX_URL", raising=False)
    monkeypatch.setattr(sandbox, "_integration", lambda: {})
    out = asyncio.run(sandbox.run_code("print(1)"))
    assert out == {"ok": False, "disabled": True}


def test_unconfigured_tool_not_advertised(monkeypatch):
    monkeypatch.delenv("VERA_SANDBOX_URL", raising=False)
    monkeypatch.setattr(sandbox, "_integration", lambda: {})
    names = [s["function"]["name"] for s in tool_protocol.tool_schemas()]
    assert "code_interpreter" not in names
    assert "web_search" in names


def test_configured_tool_advertised_last(monkeypatch):
    monkeypatch.setenv("VERA_SANDBOX_URL", "http://sandbox.example:8888")
    monkeypatch.setattr(sandbox, "_integration", lambda: {})
    names = [s["function"]["name"] for s in tool_protocol.tool_schemas()]
    assert names[-1] == "code_interpreter"


def test_timeout_ceiling(monkeypatch):
    monkeypatch.setenv("VERA_SANDBOX_TIMEOUT_S", "40")
    assert sandbox._effective_timeout(500) == 40
    assert sandbox._effective_timeout(None) == 30
    monkeypatch.setenv("VERA_SANDBOX_TIMEOUT_S", "10")
    assert sandbox._effective_timeout(None) == 10
    monkeypatch.delenv("VERA_SANDBOX_TIMEOUT_S", raising=False)
    assert sandbox._effective_timeout(999) == 120


def test_exec_happy_path(monkeypatch):
    monkeypatch.setenv("VERA_SANDBOX_URL", "http://sandbox.example:8888")
    monkeypatch.setattr(sandbox, "_integration", lambda: {})

    async def fake_session_exec(code, timeout):
        return {"ok": True, "stdout": "6\n", "result": None, "error": None}

    monkeypatch.setattr(sandbox, "_kernel_round_trip", fake_session_exec)
    out = asyncio.run(sandbox.run_code("print(2*3)"))
    assert out["ok"] and out["stdout"] == "6\n"


def test_exec_error_path(monkeypatch):
    monkeypatch.setenv("VERA_SANDBOX_URL", "http://sandbox.example:8888")
    monkeypatch.setattr(sandbox, "_integration", lambda: {})

    async def fake_session_exec(code, timeout):
        return {"ok": False, "stdout": "", "result": None, "error": "ZeroDivisionError"}

    monkeypatch.setattr(sandbox, "_kernel_round_trip", fake_session_exec)
    out = asyncio.run(sandbox.run_code("1/0"))
    assert not out["ok"] and "ZeroDivisionError" in out["error"]


def test_tool_handler_formats_output(monkeypatch):
    monkeypatch.setenv("VERA_SANDBOX_URL", "http://sandbox.example:8888")
    monkeypatch.setattr(sandbox, "_integration", lambda: {})

    async def fake_session_exec(code, timeout):
        return {"ok": True, "stdout": "hello\n", "result": "42", "error": None}

    monkeypatch.setattr(sandbox, "_kernel_round_trip", fake_session_exec)
    out = asyncio.run(sandbox._code_interpreter_tool({"code": "x"}))
    assert "hello" in out and "42" in out


def test_tool_handler_rejects_empty_code():
    out = asyncio.run(sandbox._code_interpreter_tool({}))
    assert "malformed" in out


def test_registry_dispatch_unknown_returns_none():
    assert asyncio.run(tool_protocol.dispatch("frobnicate", {})) is None
