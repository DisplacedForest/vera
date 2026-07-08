import asyncio
import json
import os
import uuid

import aiohttp
from fastapi import APIRouter
from pydantic import BaseModel

from .tool_protocol import register_tool

router = APIRouter()

DEFAULT_TIMEOUT_S = 30


def _integration() -> dict:
    try:
        from . import integrations
        return integrations.integration("sandbox") or {}
    except Exception:
        return {}


def _base() -> str:
    return (_integration().get("url") or os.environ.get("VERA_SANDBOX_URL", "")).strip().rstrip("/")


def _token() -> str:
    return (_integration().get("token") or os.environ.get("VERA_SANDBOX_TOKEN", "")).strip()


def configured() -> bool:
    return bool(_base())


def _effective_timeout(requested: int | None) -> int:
    raw = os.environ.get("VERA_SANDBOX_TIMEOUT_S", "").strip()
    try:
        ceiling = max(1, int(raw)) if raw else 120
    except ValueError:
        ceiling = 120
    return min(requested or DEFAULT_TIMEOUT_S, ceiling)


async def _kernel_round_trip(code: str, timeout: int) -> dict:
    base = _base()
    headers = {"Authorization": f"token {_token()}"} if _token() else {}
    async with aiohttp.ClientSession(headers=headers) as s:
        async with s.post(f"{base}/api/kernels", json={},
                          timeout=aiohttp.ClientTimeout(total=20)) as r:
            kernel = await r.json()
        kid = kernel["id"]
        try:
            return await asyncio.wait_for(_execute(s, base, kid, code), timeout=timeout)
        finally:
            try:
                await s.delete(f"{base}/api/kernels/{kid}",
                               timeout=aiohttp.ClientTimeout(total=10))
            except Exception:
                pass


async def _execute(s, base: str, kid: str, code: str) -> dict:
    ws_base = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    msg_id = uuid.uuid4().hex
    stdout: list[str] = []
    result = None
    error = None
    async with s.ws_connect(f"{ws_base}/api/kernels/{kid}/channels") as ws:
        await ws.send_json({
            "header": {"msg_id": msg_id, "msg_type": "execute_request",
                       "username": "vera", "session": uuid.uuid4().hex, "version": "5.3"},
            "parent_header": {}, "metadata": {}, "channel": "shell",
            "content": {"code": code, "silent": False, "store_history": False,
                        "user_expressions": {}, "allow_stdin": False},
        })
        async for frame in ws:
            if frame.type != aiohttp.WSMsgType.TEXT:
                break
            msg = json.loads(frame.data)
            if (msg.get("parent_header") or {}).get("msg_id") != msg_id:
                continue
            mtype = msg.get("msg_type") or (msg.get("header") or {}).get("msg_type")
            content = msg.get("content") or {}
            if mtype == "stream":
                stdout.append(content.get("text") or "")
            elif mtype == "execute_result":
                result = (content.get("data") or {}).get("text/plain")
            elif mtype == "error":
                error = "\n".join(content.get("traceback") or []) or content.get("evalue")
            elif mtype == "status" and content.get("execution_state") == "idle":
                break
    return {"ok": error is None, "stdout": "".join(stdout), "result": result, "error": error}


async def run_code(code: str, timeout_s: int | None = None) -> dict:
    if not configured():
        return {"ok": False, "disabled": True}
    timeout = _effective_timeout(timeout_s)
    try:
        return await _kernel_round_trip(code, timeout)
    except asyncio.TimeoutError:
        return {"ok": False, "error": f"timed out after {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class ExecRequest(BaseModel):
    code: str
    timeout_s: int | None = None


@router.post("/sandbox/exec", tags=["sandbox"])
async def exec_code(req: ExecRequest):
    return await run_code(req.code, req.timeout_s)


CODE_INTERPRETER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "code_interpreter",
        "description": ("Run Python in an isolated sandbox, for tasks no other tool covers: "
                        "computation, parsing, ad-hoc transforms. No network access. "
                        "Print whatever you need returned."),
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "the Python to execute"}},
            "required": ["code"],
        },
    },
}


async def _code_interpreter_tool(args: dict) -> str:
    code = str(args.get("code") or "").strip()
    if not code:
        return '(malformed tool arguments — call code_interpreter with JSON arguments {"code": "..."})'
    out = await run_code(code)
    if out.get("disabled"):
        return "(code_interpreter is not configured on this deployment)"
    parts = []
    if out.get("stdout"):
        parts.append(str(out["stdout"]).rstrip())
    if out.get("result"):
        parts.append(str(out["result"]))
    if out.get("error"):
        parts.append(f"error:\n{out['error']}")
    return "\n".join(parts) or "(no output)"


register_tool(CODE_INTERPRETER_SCHEMA, _code_interpreter_tool,
              available=configured, last_resort=True)
