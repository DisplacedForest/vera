"""Conversation extraction — the Profile Graph's write path.

ChatGPT Pulse's premise is that you chat normally and a model of you accrues. This job reads
new conversations from the owner's normal conversational surfaces, runs one structured-output
extraction call per conversation (the single irreducibly-LLM step), and merges the resulting
nodes/edges/threads into the Profile Graph through SER-188's deterministic dedup/decay math.

Sources are the places the owner actually thinks out loud: Open WebUI (their chats with Vera)
and ChatGPT / Claude.ai platform data exports. Coding-agent transcripts (Claude Code, Codex)
are deliberately NOT a source: a coding session is not the owner's mind, and would pollute the
model of them.

Adapters normalize every source to `{conv_id, text, ts, source}`. A per-source cursor
(extract_store) makes ingestion incremental and re-runs a no-op. All I/O is injected so the
pipeline tests offline.
"""
import glob
import json
import os
import time
from datetime import datetime, timezone

import aiohttp

from . import extract_store as es
from . import profile_graph_store as pg
from .pulse import OWUI_BASE, _headers, _vera

DUMP_ROOT = os.environ.get("CONVERSATION_DUMP_DIR", "")          # watched dir for ChatGPT/Claude.ai exports


# --------------------------------------------------------------------------- helpers


def _iso_epoch(s):
    """ISO-8601 (with a trailing Z) -> unix epoch seconds, or 0 when unparseable."""
    try:
        return int(datetime.fromisoformat(str(s).replace("Z", "+00:00"))
                   .astimezone(timezone.utc).timestamp())
    except (ValueError, TypeError):
        return 0


def _epoch_secs(v):
    """Coerce an OWUI timestamp (seconds or milliseconds, int/float/str) to epoch seconds."""
    try:
        n = float(v)
    except (ValueError, TypeError):
        return 0
    return int(n / 1000) if n > 1e12 else int(n)


def _content_text(content):
    """Flatten a message's content to text, whether it is a plain string or a block array
    (only `text` blocks contribute; tool/image blocks are skipped)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text")
    return ""


# --------------------------------------------------------------------------- adapters
# Each returns a list of normalized conversations newer than the cursor:
# {"conv_id", "text", "ts", "source"}.


def _from_chatgpt(conv):
    """One ChatGPT export conversation (a `mapping` of message nodes) -> normalized."""
    parts = []
    for node in conv.get("mapping", {}).values():
        msg = node.get("message") or {}
        role = (msg.get("author") or {}).get("role")
        if role not in ("user", "assistant"):
            continue
        text = " ".join(p for p in (msg.get("content") or {}).get("parts", []) if isinstance(p, str))
        if text.strip():
            parts.append(f"{role}: {text}")
    ts = int(conv.get("update_time") or conv.get("create_time") or 0)
    return {"conv_id": conv.get("id") or conv.get("conversation_id") or "", "source": "chatgpt",
            "text": "\n".join(parts), "ts": ts}


def _from_claude(conv):
    """One Claude export conversation (a `chat_messages` list) -> normalized."""
    parts = []
    for m in conv.get("chat_messages", []):
        sender = "user" if m.get("sender") == "human" else "assistant"
        text = m.get("text") or _content_text(m.get("content"))
        if text and text.strip():
            parts.append(f"{sender}: {text}")
    ts = _iso_epoch(conv.get("updated_at") or conv.get("created_at"))
    return {"conv_id": conv.get("uuid") or "", "source": "claude",
            "text": "\n".join(parts), "ts": ts}


def dump_conversations(cursor, root=None):
    """ChatGPT / Claude data exports dropped in a watched dir. Each JSON file is a list of
    conversations; the shape (a `mapping` vs a `chat_messages` list) selects the parser."""
    root = root or DUMP_ROOT
    if not root or not os.path.isdir(root):
        return []
    last = (cursor or {}).get("last_ts", 0)
    out = []
    for path in sorted(glob.glob(os.path.join(root, "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        for conv in data if isinstance(data, list) else []:
            if not isinstance(conv, dict):
                continue
            norm = _from_chatgpt(conv) if "mapping" in conv else \
                _from_claude(conv) if "chat_messages" in conv else None
            if norm and norm["conv_id"] and norm["text"].strip() and norm["ts"] > last:
                out.append(norm)
    return out


async def _get_json(url):
    async with aiohttp.ClientSession() as s:
        async with s.get(url, headers=_headers(), timeout=aiohttp.ClientTimeout(total=30)) as r:
            r.raise_for_status()
            return await r.json()


async def _owui_list_chats():
    return await _get_json(f"{OWUI_BASE}/api/v1/chats/list")


async def _owui_get_chat(cid):
    return await _get_json(f"{OWUI_BASE}/api/v1/chats/{cid}")


def _owui_messages_text(chat):
    """Flatten an OWUI chat record's messages to text. Handles the `messages` list shape and
    the `history.messages` dict shape."""
    body = chat.get("chat", chat) if isinstance(chat, dict) else {}
    msgs = body.get("messages")
    if not msgs and isinstance(body.get("history"), dict):
        msgs = list(body["history"].get("messages", {}).values())
    parts = []
    for m in msgs or []:
        role = m.get("role") or "?"
        text = _content_text(m.get("content"))
        if text and text.strip():
            parts.append(f"{role}: {text}")
    return "\n".join(parts)


async def owui_conversations(cursor, list_fn=None, chat_fn=None):
    """Open WebUI chats, incremental by `updated_at`. The list/fetch calls are injected so the
    adapter tests offline; in production they hit the OWUI chat API."""
    list_fn = list_fn or _owui_list_chats
    chat_fn = chat_fn or _owui_get_chat
    last = (cursor or {}).get("last_ts", 0)
    out = []
    for meta in (await list_fn()) or []:
        ts = _epoch_secs(meta.get("updated_at") or meta.get("timestamp"))
        if ts <= last:
            continue
        text = _owui_messages_text(await chat_fn(meta["id"]))
        if text.strip():
            out.append({"conv_id": meta["id"], "text": text, "ts": ts, "source": "owui"})
    return out


# --------------------------------------------------------------------------- extraction (LLM)


EXTRACT_SYS = (
    "You read one conversation and extract a structured model of the person you are learning "
    "about. Pull out: their interests, projects, goals, the people/companies/places/assets they "
    "mention, and any open question or unresolved thread. Be conservative: only what the "
    "conversation actually supports, no speculation.\n"
    "Node types: project, interest, goal, person, company, location, asset. For each node give a "
    "short canonical `label`, any durable `facts` (short strings), an `engagement_signal` 0-1 "
    "(how strongly THIS conversation engaged it), and a `confidence` 0-1.\n"
    "Edges connect node labels: supports, depends_on, related_to, part_of, about, at_location.\n"
    "Threads are open questions the person has not resolved (status open), or ones this "
    "conversation resolves (status resolved).\n"
    'Return ONLY JSON: {"nodes":[{"type":"...","label":"...","facts":["..."],'
    '"engagement_signal":N,"confidence":N}],"edges":[{"src":"...","dst":"...","type":"..."}],'
    '"threads":[{"question":"...","status":"open|resolved"}]}.'
)

_EMPTY = {"nodes": [], "edges": [], "threads": []}


def _parse_json(txt):
    try:
        return json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return {}


async def extract(text):
    """The one irreducibly-LLM step: a conversation -> structured nodes/edges/threads. Returns
    the empty shape on any parse failure, so a bad extraction skips the conversation rather than
    breaking the run."""
    raw = await _vera([{"role": "system", "content": EXTRACT_SYS},
                       {"role": "user", "content": text}], temperature=0.2)
    parsed = _parse_json(raw)
    if not isinstance(parsed, dict):
        return dict(_EMPTY)
    return {"nodes": parsed.get("nodes") or [],
            "edges": parsed.get("edges") or [],
            "threads": parsed.get("threads") or []}


# --------------------------------------------------------------------------- merge into the graph

NODE_TYPES = {"project", "interest", "goal", "person", "company", "location", "asset"}
EDGE_TYPES = {"supports", "depends_on", "related_to", "part_of", "about", "at_location"}


async def merge_conversation(conv, extracted, now=None):
    """Land one conversation's extracted nodes/edges/threads in the graph through SER-188's
    dedup math. Facts are stamped with this conversation's id/time; engagement is bumped by the
    extraction's per-node signal; edges connect the labels seen here; threads become thread
    nodes whose state flips to resolved when a later conversation closes them. Returns counts."""
    now = int(time.time()) if now is None else now
    cid, cts = conv.get("conv_id"), conv.get("ts") or now
    label_to_id = {}
    for n in extracted.get("nodes", []):
        label = (n.get("label") or "").strip()
        ntype = n.get("type")
        if not label or ntype not in NODE_TYPES:
            continue
        facts = [pg.make_fact(f, source=f"extraction:{cid}", observed_at=cts)
                 for f in (n.get("facts") or []) if isinstance(f, str) and f.strip()]
        emb = await pg.embed(label)
        nid = pg.merge_or_create(type=ntype, label=label, embedding=emb, facts=facts, now=now,
                                 recency_factor=float(n.get("engagement_signal") or 1.0))
        label_to_id[label] = nid
    edges = 0
    for e in extracted.get("edges", []):
        s = label_to_id.get((e.get("src") or "").strip())
        d = label_to_id.get((e.get("dst") or "").strip())
        if s and d and e.get("type") in EDGE_TYPES:
            pg.add_edge(s, d, e["type"])
            edges += 1
    threads = 0
    for t in extracted.get("threads", []):
        q = (t.get("question") or "").strip()
        if not q:
            continue
        state = "resolved" if t.get("status") == "resolved" else "open"
        pg.upsert_by_label(type="thread", label=q, state=state)
        threads += 1
    return {"nodes": len(label_to_id), "edges": edges, "threads": threads}


# --------------------------------------------------------------------------- the job


async def _gather():
    """All new conversations across every configured source, each filtered against its own
    per-source cursor. The OWUI adapter filters internally; the dump adapter carries two
    sources (ChatGPT, Claude.ai), so it is fetched wide and re-filtered per source here."""
    convs = []
    dump_floor = min(es.get_cursor("chatgpt")["last_ts"], es.get_cursor("claude")["last_ts"])
    for c in dump_conversations({"last_ts": dump_floor, "last_id": None}):
        if c["ts"] > es.get_cursor(c["source"])["last_ts"]:
            convs.append(c)
    if OWUI_BASE:
        convs += await owui_conversations(es.get_cursor("owui"))
    return convs


async def run():
    """Ingest every new conversation: extract -> merge -> advance the per-source cursors.
    Oldest-first so a thread opened in one conversation can be resolved by a later one in the
    same run. Returns aggregate counts."""
    out = {"conversations": 0, "nodes": 0, "edges": 0, "threads": 0}
    max_ts = {}
    for conv in sorted(await _gather(), key=lambda c: c.get("ts") or 0):
        extracted = await extract(conv["text"])
        m = await merge_conversation(conv, extracted)
        out["conversations"] += 1
        out["nodes"] += m["nodes"]
        out["edges"] += m["edges"]
        out["threads"] += m["threads"]
        src = conv["source"]
        max_ts[src] = max(max_ts.get(src, 0), conv.get("ts") or 0)
    for src, ts in max_ts.items():
        es.set_cursor(src, ts)
    return out
