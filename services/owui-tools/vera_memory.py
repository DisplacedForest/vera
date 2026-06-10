"""
title: Vera Memory
author: vera
description: Vera's own world-model memory. recall_memory looks up what she's previously learned/concluded (deeper than the always-injected core); remember saves a new learning or a scratch note. This is HER memory (distinct from facts about the user or the home). Writing is free — it's her own knowledge.
version: 0.1.0
"""
import requests
from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        vera_api_url: str = Field(default="http://localhost:8089")

    def __init__(self):
        self.valves = self.Valves()

    async def recall_memory(self, query: str = "", __event_emitter__=None) -> str:
        """
        Recall what you (Vera) have previously learned or concluded about a topic — your own
        world-model, beyond the core that's always in your context. Use when you need the detail
        behind a belief, or to check whether you already know something before researching it again.
        """
        async def emit(d, done=False):
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": d, "done": done}})

        await emit("Recalling...")
        try:
            rows = requests.get(f"{self.valves.vera_api_url}/memory/self/recall",
                                params={"q": query or None}, timeout=20).json().get("results", [])
        except Exception as e:
            await emit("Memory unreachable", True)
            return f"Could not reach my memory: {e}"
        await emit("Recalled", True)
        if not rows:
            return "I have nothing on record about that yet."
        return "\n".join(
            f"- [{r['tier']}] {r['content']}" + (f"  ({r['topic']})" if r.get("topic") else "")
            for r in rows
        )

    async def remember(self, topic: str, content: str, tier: str = "archive",
                       __event_emitter__=None) -> str:
        """
        Save something YOU learned or concluded to your own memory (free — no confirmation needed,
        it's your knowledge). `tier`: "archive" for durable learnings (default), "core" for a
        high-impact belief that should color all your reasoning, or "scratch" for an ephemeral
        working note (a scribble pad that auto-expires). `topic` is a short label.
        """
        async def emit(d, done=False):
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": d, "done": done}})

        await emit("Noting it...")
        try:
            r = requests.post(f"{self.valves.vera_api_url}/memory/self/write",
                              json={"topic": topic, "content": content, "tier": tier,
                                    "source": "vera"}, timeout=20).json()
        except Exception as e:
            await emit("Memory unreachable", True)
            return f"Could not save to my memory: {e}"
        await emit("Noted", True)
        return f"Saved to {tier} memory ({r.get('id')})."
