"""
title: Deep Research
author: vera
description: Multi-source cited research report — browse, gather, and synthesize across many web sources plus Vera's local knowledge, with inline citations. For thorough/sourced questions ("research X", "deep dive", "survey", "compare"), not quick lookups. Takes 1-3 minutes.
version: 0.1.0
"""
import requests
from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        vera_api_url: str = Field(
            default="http://localhost:8089",
            description="Base URL of vera-api (hosts the /research endpoint).",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def deep_research(self, query: str, __event_emitter__=None) -> str:
        """
        Run a deep, multi-source research pass and return a cited report. Use when the user wants a
        thorough, well-sourced answer (research, deep dive, literature/market survey, compare across
        sources) rather than a quick fact (use web_search for those). Takes 1-3 minutes.
        :param query: The research question to investigate.
        """
        async def emit(desc, done=False):
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": desc, "done": done}})

        await emit(f"Deep research: {query} …")
        try:
            d = requests.post(f"{self.valves.vera_api_url}/research", json={"query": query}, timeout=300).json()
        except Exception as e:
            await emit("Research failed", True)
            return f"Deep research failed: {e}"
        n = len(d.get("sources", []))
        await emit(f"Synthesized from {n} sources in {d.get('seconds', '?')}s", True)
        return d.get("report") or "No report produced (no sources gathered)."
