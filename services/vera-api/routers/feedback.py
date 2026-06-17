"""Feedback log + write-back — human reactions on Pulse cards and chat responses.

Appends one JSON line per rating to feedback.jsonl (the seed of a human-preference dataset)
AND, when a `card_id` is given, writes the reaction back into the Profile Graph: the served
node(s)' engagement moves by the signal's effect (`learn.apply_signal`), closing the loop so
the feed learns from what the owner does with it.

Persisted under a mounted data dir (FEEDBACK_PATH) so it survives container rebuilds.
"""

import json
import os
import time

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

FEEDBACK_PATH = os.environ.get("FEEDBACK_PATH", "/data/feedback.jsonl")


class Feedback(BaseModel):
    kind: str                    # "pulse" | "chat"
    sentiment: str               # "up" | "down"
    topic: str | None = None
    title: str | None = None
    content: str | None = None   # the rated text (response body / card body)
    chat_id: str | None = None
    message_id: str | None = None
    model: str | None = None
    card_id: str | None = None   # the Pulse card this reaction is on (enables graph write-back)
    signal: str | None = None    # explicit signal (open/bookmark/promote/expire); else sentiment


@router.post("/feedback", tags=["feedback"])
async def submit(fb: Feedback):
    rec = fb.model_dump()
    rec["ts"] = int(time.time())
    os.makedirs(os.path.dirname(FEEDBACK_PATH) or ".", exist_ok=True)
    with open(FEEDBACK_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    written = None
    if fb.card_id:
        from . import learn
        written = learn.apply_signal(fb.card_id, fb.signal or fb.sentiment)
    return {"ok": True, "write_back": written}


@router.get("/feedback/summary", tags=["feedback"])
async def summary():
    """Per-topic up/down tallies — the raw material for later triage weighting."""
    counts: dict[str, dict[str, int]] = {}
    total = 0
    try:
        with open(FEEDBACK_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                total += 1
                key = r.get("topic") or r.get("title") or "(none)"
                d = counts.setdefault(key, {"up": 0, "down": 0})
                if r.get("sentiment") in d:
                    d[r["sentiment"]] += 1
    except FileNotFoundError:
        pass
    return {"total": total, "topics": counts}
