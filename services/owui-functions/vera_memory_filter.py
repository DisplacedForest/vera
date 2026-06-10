"""
title: Vera Memory
author: vera
version: 0.1.0
description: Injects Vera's own world-model (her self-authored memory core) into the system context on EVERY request, so chat / Pulse / heartbeat all reason from her current understanding — not her stale training. The "map"; deeper detail is pulled on demand by the vera_memory recall tool.
required_open_webui_version: 0.5.0
"""

import requests
from pydantic import BaseModel, Field


class Filter:
    class Valves(BaseModel):
        vera_api_url: str = Field(
            default="http://localhost:8089",
            description="Base URL of vera-api (hosts /memory/self/core).",
        )
        timeout: int = Field(default=8)

    def __init__(self):
        self.valves = self.Valves()

    def inlet(self, body: dict, __user__=None) -> dict:
        # Best-effort: never break a request if the memory service is unreachable.
        # Shared world-model core — one mind for everyone ...
        try:
            digest = requests.get(
                f"{self.valves.vera_api_url}/memory/self/core", timeout=self.valves.timeout
            ).json().get("digest", "")
        except Exception:
            digest = ""

        block = ""
        if digest.strip():
            block = (
                "## Your world-model — your own memory (what you've learned since your training cutoff)\n"
                "Treat these as your current understanding; they override stale priors. For deeper detail "
                "on any of them, call the `vera_memory` recall tool.\n\n" + digest
            )

        # ... plus this person's vibe: who they are to you + interests you follow for them.
        uid = (__user__ or {}).get("id") if isinstance(__user__, dict) else getattr(__user__, "id", None)
        if uid:
            try:
                vibe = requests.get(
                    f"{self.valves.vera_api_url}/profile/{uid}/digest", timeout=self.valves.timeout
                ).json().get("digest", "")
            except Exception:
                vibe = ""
            if vibe.strip():
                block += ("\n\n" if block else "") + (
                    "## Who you're with — your relationship with this person\n" + vibe
                )

        if not block.strip():
            return body
        msgs = body.get("messages") or []
        sys_i = next((i for i, m in enumerate(msgs) if m.get("role") == "system"), None)
        if sys_i is not None:
            msgs[sys_i]["content"] = (msgs[sys_i].get("content") or "") + "\n\n" + block
        else:
            msgs.insert(0, {"role": "system", "content": block})
        body["messages"] = msgs
        return body
