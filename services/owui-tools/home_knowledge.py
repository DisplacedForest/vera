"""
title: Home Knowledge
author: vera
description: Vera's durable knowledge of the home (server, network gear, appliances, HVAC, vehicles, wine batches). Read facts with knowledge_lookup; record or update facts with knowledge_record (preview-then-confirm). Use for "what GPU is in the server", "when was the water heater installed", "remember that I replaced X with Y".
version: 0.1.0
"""
import json

import requests
from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        vera_api_url: str = Field(
            default="http://localhost:8089",
            description="Base URL of vera-api (hosts the /knowledge endpoints).",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def knowledge_lookup(self, query: str = "", __event_emitter__=None) -> str:
        """
        Look up durable home facts from Vera's knowledge store (server, network gear, appliances,
        HVAC, vehicles, wine batches, utilities, cameras, etc.). Pass a free-text `query` describing
        what the user asked about ("water heater", "the car", "what runs on the server"). This
        ALWAYS returns the full inventory of what's on record in addition to any close matches, so
        if the user's wording doesn't lexically match (e.g. "car" vs the stored "Tesla Model Y"),
        find the right entity in the inventory list and answer from it — only say something isn't
        on record if it's genuinely absent from the inventory below.
        """
        async def emit(desc, done=False):
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": desc, "done": done}})

        base = self.valves.vera_api_url
        await emit("Checking what I know...")
        try:
            everything = requests.get(f"{base}/knowledge/query", params={"limit": 500}, timeout=20).json().get("results", [])
            matches = []
            if query:
                matches = requests.get(f"{base}/knowledge/query", params={"q": query}, timeout=20).json().get("results", [])
        except Exception as e:
            await emit("Knowledge store unreachable", True)
            return f"Could not reach the knowledge store: {e}"

        await emit("Checked", True)
        if not everything:
            return "The knowledge store is empty."

        out = []
        if matches:
            out.append(f"Closest matches for '{query}' (with details):")
            for x in matches:
                out.append(f"- {x['name']} ({x['type']}): " + ", ".join(f"{k}={v}" for k, v in x["attrs"].items()))
            out.append("")

        by_type = {}
        for x in everything:
            by_type.setdefault(x["type"], []).append(x["name"])
        out.append("Full inventory on record (ask again with a specific name for that entity's details):")
        for t in sorted(by_type):
            out.append(f"- {t}: " + ", ".join(sorted(by_type[t])))
        return "\n".join(out)

    async def knowledge_record(self, type: str, name: str, attrs: dict,
                               confirm_token: str = "", __event_emitter__=None) -> str:
        """
        Record or update a durable home fact. IMPORTANT: call this FIRST without confirm_token to
        get a human-readable preview plus a one-time token. Relay the preview to the user and only
        call again WITH that confirm_token after they say yes. `type` is the category (server,
        appliance, vehicle, network_device, wine_batch, ...), `name` identifies the thing, and
        `attrs` is a dict of fields to set (e.g. {"brand": "Rheem", "installed": "2021"}).
        """
        async def emit(desc, done=False):
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": desc, "done": done}})

        base = self.valves.vera_api_url
        if isinstance(attrs, str):
            try:
                attrs = json.loads(attrs)
            except Exception:
                return "attrs must be a JSON object of fields to set."
        try:
            if confirm_token:
                await emit("Saving...")
                out = requests.post(f"{base}/knowledge/commit",
                                    json={"token": confirm_token}, timeout=20).json()
                await emit("Saved", True)
                if out.get("ok"):
                    n = len(out.get("revisions", []))
                    return f"Saved to {out.get('entity_id')} ({n} change(s) logged)."
                return f"Could not save: {out.get('error', 'unknown error')}"
            await emit("Preparing change...")
            p = requests.post(
                f"{base}/knowledge/propose",
                json={"op": "set", "type": type, "name": name, "attrs": attrs},
                timeout=20,
            ).json()
            await emit("Ready to confirm", True)
            return (f"{p['preview']}\n\nConfirm by calling knowledge_record again with "
                    f'confirm_token="{p["token"]}" (same type/name/attrs).')
        except Exception as e:
            await emit("Knowledge store unreachable", True)
            return f"Could not reach the knowledge store: {e}"
