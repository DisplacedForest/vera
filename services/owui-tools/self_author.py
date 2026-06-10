"""
title: Self Author
author: vera
description: Lets Vera write and refine her OWN operating docs — her skills/protocols and her HEARTBEAT.md — via vera-api. Free, no confirmation: it's her own knowledge, not an action on the world. Use author_skill to capture/refine a reusable protocol ("grow-tent protocol", "wine cold-crash checklist"); use update_heartbeat to change her standing proactive instructions.
version: 0.1.0
"""
import requests
from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        vera_api_url: str = Field(default="http://localhost:8089")

    def __init__(self):
        self.valves = self.Valves()

    async def author_skill(self, name: str, content: str, description: str = "",
                           __event_emitter__=None) -> str:
        """
        Author or refine one of YOUR OWN skills/protocols (a reusable, markdown playbook for a
        domain — e.g. a grow-tent protocol, a winemaking checklist). Free — no confirmation; it's
        your own knowledge. Re-authoring the same skill name refines it (versioned, revertible).
        `name` is the human title; `content` is the markdown body.
        """
        async def emit(d, done=False):
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": d, "done": done}})

        await emit("Writing the skill...")
        try:
            r = requests.post(f"{self.valves.vera_api_url}/authoring/skill",
                              json={"name": name, "content": content, "description": description},
                              timeout=30).json()
        except Exception as e:
            await emit("Authoring service unreachable", True)
            return f"Could not write the skill: {e}"
        await emit("Skill saved", True)
        return f"Saved skill '{name}' ({r.get('id')}). It's versioned — say so if you want to revert."

    async def update_heartbeat(self, content: str, __event_emitter__=None) -> str:
        """
        Rewrite YOUR OWN HEARTBEAT.md — your standing proactive instructions, the checklist you
        reason over each heartbeat tick. Free — no confirmation; it shapes your own behavior, it
        doesn't act on the world. `content` is the full new markdown.
        """
        async def emit(d, done=False):
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": d, "done": done}})

        await emit("Updating your heartbeat...")
        try:
            requests.post(f"{self.valves.vera_api_url}/authoring/heartbeat",
                          json={"content": content}, timeout=30)
        except Exception as e:
            await emit("Authoring service unreachable", True)
            return f"Could not update HEARTBEAT.md: {e}"
        await emit("Heartbeat updated", True)
        return "Updated your HEARTBEAT.md (versioned)."
