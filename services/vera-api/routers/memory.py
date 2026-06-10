"""Memory groomer — retire episodic memories whose `[Expires: YYYY-MM-DD]` has passed.

Rules-based and autonomous: it only deletes memories that carry an expiry marker whose
date is strictly before today. Durable memories (no marker) are never touched. Adaptive
Memory v3's own consolidation handles duplicate-merging; this only does expiry.

The built-in scheduler fires POST /memory/groom nightly. Pass ?dry_run=true to preview without deleting.
"""

import datetime
import os
import re

import aiohttp
from fastapi import APIRouter, HTTPException

router = APIRouter()

OWUI_BASE = os.environ.get("OWUI_BASE", "").rstrip("/")
OWUI_KEY = os.environ.get("OWUI_KEY", "")

# matches a trailing-or-inline [Expires: 2026-06-14] marker
EXPIRES_RE = re.compile(r"\[Expires:\s*(\d{4}-\d{2}-\d{2})\s*\]")


def _headers():
    return {"Authorization": f"Bearer {OWUI_KEY}", "Content-Type": "application/json"}


@router.post("/memory/groom", tags=["memory"])
async def groom(dry_run: bool = False):
    if not OWUI_BASE or not OWUI_KEY:
        raise HTTPException(status_code=503,
                            detail="memory groom requires Open WebUI — set OWUI_BASE and OWUI_KEY")
    # ISO date strings compare chronologically, so plain string < works.
    today = datetime.date.today().isoformat()
    out = {
        "ok": True,
        "today": today,
        "dry_run": dry_run,
        "removed": [],
        "kept": 0,
        "errors": [],
    }

    async with aiohttp.ClientSession() as s:
        try:
            async with s.get(
                f"{OWUI_BASE}/api/v1/memories/",
                headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                mems = await r.json()
        except Exception as e:
            out["ok"] = False
            out["errors"].append(f"list memories: {e}")
            return out

        for m in mems or []:
            content = m.get("content") or ""
            mid = m.get("id")
            match = EXPIRES_RE.search(content)
            if match and match.group(1) < today:
                if dry_run:
                    out["removed"].append({"id": mid, "expired_on": match.group(1), "content": content})
                    continue
                try:
                    async with s.delete(
                        f"{OWUI_BASE}/api/v1/memories/{mid}",
                        headers=_headers(),
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as dr:
                        if dr.status < 300:
                            out["removed"].append(
                                {"id": mid, "expired_on": match.group(1), "content": content}
                            )
                        else:
                            out["errors"].append(f"{mid}: delete HTTP {dr.status}")
                except Exception as e:
                    out["errors"].append(f"{mid}: {e}")
            else:
                out["kept"] += 1

    out["removed_count"] = len(out["removed"])
    return out
