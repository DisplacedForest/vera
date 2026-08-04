import logging
import os
import time

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from . import conversation_extract as ce
from . import extract_store as es

router = APIRouter()
log = logging.getLogger("vera.ingest")

AGENT_TOKEN = os.environ.get("KNOWLEDGE_AGENT_TOKEN", "")
SOURCE = "native"
MAX_BATCH = 200


class IngestMessage(BaseModel):
    role: str
    text: str


class IngestConversation(BaseModel):
    conv_id: str
    ts: int
    messages: list[IngestMessage] = Field(default_factory=list)


class IngestBody(BaseModel):
    conversations: list[IngestConversation]


def _text(conv: IngestConversation) -> str:
    return "\n".join(f"{m.role}: {m.text}" for m in conv.messages
                     if m.text and m.text.strip())


@router.post("/conversations/ingest", tags=["conversations"])
async def ingest(b: IngestBody, x_agent_token: str = Header(default="")):
    if not AGENT_TOKEN or x_agent_token != AGENT_TOKEN:
        raise HTTPException(403, "conversation ingest requires X-Agent-Token")
    if len(b.conversations) > MAX_BATCH:
        raise HTTPException(422, f"batch too large: {len(b.conversations)} > {MAX_BATCH}")
    out = {"ok": True, "ingested": 0, "duplicates": 0, "empty": 0, "failed": 0,
           "nodes": 0, "edges": 0, "threads": 0}
    now = int(time.time())
    for conv in sorted(b.conversations, key=lambda c: c.ts):
        cid = conv.conv_id.strip()
        if not cid:
            out["empty"] += 1
            continue
        if es.seen(SOURCE, cid):
            out["duplicates"] += 1
            continue
        text = _text(conv)
        if not text.strip():
            out["empty"] += 1
            continue
        ts = min(conv.ts or now, now)
        try:
            extracted = await ce.extract(text)
            m = await ce.merge_conversation(
                {"conv_id": cid, "text": text, "ts": ts, "source": SOURCE}, extracted)
        except Exception:
            log.exception("native ingest failed for %s", cid)
            out["failed"] += 1
            continue
        es.mark_seen(SOURCE, cid, ts)
        es.set_cursor(SOURCE, max(es.get_cursor(SOURCE)["last_ts"], ts), cid)
        out["ingested"] += 1
        out["nodes"] += m["nodes"]
        out["edges"] += m["edges"]
        out["threads"] += m["threads"]
    return out
