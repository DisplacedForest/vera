"""
title: Media Request
author: vera
description: Vera's control of the household media library via Overseerr (Plex + the *arr stack). Search for a movie/show and see if it's already in the library, then request it to start downloading. Call when the user asks to add/get/download a movie or show, or asks whether something is already available.
version: 0.1.0
"""
import requests
from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        vera_api_url: str = Field(
            default="http://localhost:8089",
            description="Base URL of vera-api (hosts the /overseerr endpoints).",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def search_media(self, query: str = "", __event_emitter__=None) -> str:
        """
        Search the media library for a movie or show by title. Returns matches, each with its
        tmdb id, type (movie/tv), year, and availability (available = already in Plex,
        requested/processing = already on the way, not_requested = can be added). Use this FIRST
        whenever the user asks whether something is in the library, or before requesting anything
        so you have the correct id. Relay availability honestly; only request a title whose
        availability is not_requested.
        """
        async def emit(desc, done=False):
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": desc, "done": done}})

        if not query.strip():
            return "Give me a title to search for."
        await emit(f"Searching the library for '{query}'...")
        try:
            r = requests.get(f"{self.valves.vera_api_url}/overseerr/search",
                             params={"q": query}, timeout=20).json()
        except Exception as e:
            await emit("Media service unreachable", True)
            return f"Could not reach the media service: {e}"

        results = r.get("results") or []
        await emit("Searched", True)
        if not results:
            return f"No matches for '{query}'."
        lines = [f"Matches for '{query}':"]
        for m in results:
            yr = f" ({m['year']})" if m.get("year") else ""
            lines.append(f"- {m['title']}{yr} [{m['media_type']}] · id={m['id']} · {m['availability']}")
        return "\n".join(lines)

    async def request_media(self, media_type: str, media_id: int, __event_emitter__=None) -> str:
        """
        Request a movie or show so it starts downloading. `media_type` is "movie" or "tv";
        `media_id` is the tmdb id from search_media. TV requests grab all seasons in HD. This
        actuates immediately when the user explicitly asks for it (no confirmation needed) — but
        always run search_media first to confirm the id and that it isn't already available.
        """
        async def emit(desc, done=False):
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": desc, "done": done}})

        if media_type not in ("movie", "tv"):
            return 'media_type must be "movie" or "tv".'
        await emit("Submitting the request...")
        try:
            r = requests.post(f"{self.valves.vera_api_url}/overseerr/request",
                              json={"media_type": media_type, "media_id": int(media_id)},
                              timeout=20).json()
        except Exception as e:
            await emit("Media service unreachable", True)
            return f"Could not reach the media service: {e}"

        await emit("Requested", True)
        if r.get("ok"):
            return f"Requested ({media_type}, id {media_id}). It's queued and will start downloading."
        return f"Could not request that: {r.get('detail', r)}"
