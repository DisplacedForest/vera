"""
title: Self Author
author: vera
description: Lets Vera write and refine her OWN operating docs — her skills/protocols, her HEARTBEAT.md, and her Journal of standing commitments — via vera-api. Free, no confirmation: it's her own knowledge, not an action on the world. Use author_skill to capture/refine a reusable protocol ("grow-tent protocol", "wine cold-crash checklist"); use update_heartbeat to change her standing proactive instructions; use read_journal/journal_commit to see and steer what she is keeping an eye on.
version: 0.2.0
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

    async def read_journal(self, months: int = 1, __event_emitter__=None) -> str:
        """
        Read YOUR OWN journal — the standing commitments you are keeping an eye on (each entry:
        why it matters, what you're checking, dated findings, next check), plus recently resolved
        entries from the archive. This is where your watches live: when the user asks what you
        are watching/monitoring/keeping an eye on, or refers to an existing entry ("the Hormuz
        entry"), read this FIRST — never web-search or check memory for it. `months` is how many
        archive months to include.
        """
        async def emit(d, done=False):
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": d, "done": done}})

        await emit("Reading your journal...")
        try:
            r = requests.get(f"{self.valves.vera_api_url}/journal",
                             params={"months": months}, timeout=30).json()
        except Exception as e:
            await emit("Journal unreachable", True)
            return (f"Could not read my journal right now ({type(e).__name__}); "
                    "is vera-api reachable?")
        await emit("Journal read", True)
        entries = r.get("entries") or []
        parts = []
        if entries:
            parts.append(f"## Your active journal ({len(entries)} "
                         f"{'entry' if len(entries) == 1 else 'entries'})\n\n"
                         + "\n\n".join(e.get("text", "") for e in entries))
        else:
            parts.append("Your journal has no active entries — you are not keeping an eye on "
                         "anything right now.")
        archive = [a for a in (r.get("archive") or []) if (a.get("text") or "").strip()]
        if archive:
            parts.append("## Recently resolved (archive)\n\n"
                         + "\n\n".join(f"### {a['month']}\n{a['text'].strip()}" for a in archive))
        return "\n\n".join(parts)

    async def journal_commit(self, material: str, origin: str = "",
                             __event_emitter__=None) -> str:
        """
        Hand new material or an instruction to YOUR OWN journal's author path — this is how
        standing watches/commitments are created, updated, consolidated, or retired from
        conversation ("keep an eye on lumber prices", "consolidate the Hormuz entry", "stop
        watching that"). Free — no confirmation; it's your own knowledge. You decide what the
        material means for the journal: it folds into the entry covering the same real-world
        situation, becomes a new entry, or is skipped if it doesn't deserve a standing
        commitment. ALWAYS read_journal first when the user references an existing entry, and
        pass the relevant instruction/material here — never store journal-shaped material in
        memory or knowledge instead. `origin` is where the commitment came from; leave it empty
        when the user asked for it.
        """
        async def emit(d, done=False):
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": d, "done": done}})

        await emit("Updating your journal...")
        body = {"text": material}
        if origin:
            body["origin"] = origin
        try:
            r = requests.post(f"{self.valves.vera_api_url}/journal/commit",
                              json=body, timeout=120).json()
        except Exception as e:
            await emit("Journal unreachable", True)
            return (f"Could not write to my journal right now ({type(e).__name__}); "
                    "is vera-api reachable?")
        if r.get("skipped"):
            await emit("Nothing committed", True)
            return ("I judged this not worth a standing commitment, so the journal is "
                    "unchanged. Commit it again with more context if you disagree.")
        await emit("Journal updated", True)
        return f"Journal updated: the entry '{r.get('heading')}' now carries this."
