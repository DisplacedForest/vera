"""
title: Propose Action
author: vera
description: Propose an action as a Pulse card that the owner confirms in the app before it runs. Use when you want to DO something that should be confirmed first — set the thermostat, record a durable fact, adjust kitchen stock, or run a health check. Nothing executes until they Confirm the card.
version: 0.1.0
"""
import json

import requests
from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        vera_api_url: str = Field(
            default="http://localhost:8089",
            description="Base URL of vera-api (hosts the /actions endpoints).",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def list_action_verbs(self, __event_emitter__=None) -> str:
        """
        The catalog of action verbs the server currently supports: each verb's purpose, its
        exact args shape, risk, and the deployment's configured allowlist. Call this FIRST
        whenever you are unsure which verb to use or how to shape its args.
        """
        try:
            out = requests.get(f"{self.valves.vera_api_url}/actions/registry", timeout=10).json()
        except Exception as e:
            return f"Could not reach vera-api: {e}"
        lines = []
        for v in out.get("verbs", []):
            lines.append(f"- {v['verb']}  args: {v['args']}  — {v['summary']} "
                         f"(risk {v['risk']}, {'reversible' if v['reversible'] else 'NOT reversible'})")
        allow = out.get("ha_allowlist") or {}
        if allow:
            lines.append(f"ha.service allowlist (deployment config): "
                         f"services {', '.join(allow.get('services', [])) or 'none'}; "
                         f"whole domains {', '.join(allow.get('domains', [])) or 'none'}")
        return "\n".join(lines) or "No verbs available."

    async def propose_action(self, verb: str, args: dict, title: str, body: str,
                             __event_emitter__=None) -> str:
        """
        Stage an action and post it to Pulse as a confirm-able card. The owner Confirms or Dismisses
        it in the app; NOTHING runs until they confirm. Returns the human preview.

        verb and args must match the server's catalog — call list_action_verbs first if you are
        not certain of the verb name or args shape; the server validates and refuses anything
        unknown or outside the deployment's allowlist.
        title: a short card title.  body: a short markdown explanation of what it does and why.
        """
        async def emit(desc, done=False):
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": desc, "done": done}})

        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                return "args must be a JSON object."

        await emit("Proposing action...")
        try:
            out = requests.post(
                f"{self.valves.vera_api_url}/actions/propose_card",
                json={"verb": verb, "args": args, "title": title, "body": body},
                timeout=20,
            ).json()
        except Exception as e:
            await emit("vera-api unreachable", True)
            return f"Could not reach vera-api: {e}"

        if not out.get("ok"):
            await emit("Rejected", True)
            return f"Action not proposed: {out.get('error', 'unknown error')}"

        await emit("Card posted to Pulse", True)
        return (
            f"Posted an action card to Pulse: {out['preview']} "
            f"(risk {out['risk']}, {'reversible' if out['reversible'] else 'NOT reversible'}). "
            f"It can be Confirmed or Dismissed in the app."
        )
