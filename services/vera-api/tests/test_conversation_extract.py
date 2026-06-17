"""Conversation extraction — source adapters, the structured-output extraction call, and the
merge into the Profile Graph. All I/O (OWUI fetch, LLM, embeddings) is injected/mocked, so the
suite is offline and deterministic. Run under pytest."""
import asyncio
import json
import os

import pytest

from routers import conversation_extract as ce
from routers import profile_graph_store as pg


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    pg.DB_PATH = os.path.join(str(tmp_path), "graph.db")
    pg.init()
    from routers import extract_store as es
    es.DB_PATH = os.path.join(str(tmp_path), "extract.db")
    es.init()
    yield


# --------------------------------------------------------------- export dump adapter

def test_old_conversation_seeds_a_decayed_node():
    import time
    from routers import scout
    now = int(time.time())
    old = now - 700 * 86400          # ~2 years ago
    extracted = {"nodes": [{"type": "interest", "label": "old hobby", "facts": [],
                            "engagement_signal": 1.0}], "edges": [], "threads": []}
    asyncio.run(ce.merge_conversation({"conv_id": "old", "ts": old}, extracted, now=now))
    node = pg.node_by_label("interest", "old hobby")
    assert pg.engagement_now(node, now) < scout.ENGAGEMENT_FLOOR   # aged out, not scouted


def test_recent_conversation_seeds_a_live_node():
    import time
    from routers import scout
    now = int(time.time())
    extracted = {"nodes": [{"type": "interest", "label": "fresh topic", "facts": [],
                            "engagement_signal": 1.0}], "edges": [], "threads": []}
    asyncio.run(ce.merge_conversation({"conv_id": "new", "ts": now - 86400}, extracted, now=now))
    node = pg.node_by_label("interest", "fresh topic")
    assert pg.engagement_now(node, now) >= scout.ENGAGEMENT_FLOOR  # recent → live


def test_dump_adapter_parses_chatgpt_export(tmp_path):
    d = tmp_path / "dumps"
    d.mkdir()
    (d / "chatgpt.json").write_text(json.dumps([{
        "id": "cg1", "title": "Orchard", "update_time": 1_717_000_000.0,
        "mapping": {
            "a": {"message": {"author": {"role": "user"},
                              "content": {"content_type": "text", "parts": ["plant hazelnuts"]}}},
            "b": {"message": {"author": {"role": "assistant"},
                              "content": {"content_type": "text", "parts": ["good idea"]}}},
        }}]))
    out = ce.dump_conversations({"last_ts": 0, "last_id": None}, root=str(d))
    assert len(out) == 1
    c = out[0]
    assert c["conv_id"] == "cg1" and c["source"] == "chatgpt"
    assert "plant hazelnuts" in c["text"] and "good idea" in c["text"]
    assert c["ts"] == 1_717_000_000


def test_dump_adapter_parses_claude_export(tmp_path):
    d = tmp_path / "dumps"
    d.mkdir()
    (d / "claude.json").write_text(json.dumps([{
        "uuid": "cl1", "name": "Wine", "updated_at": "2026-05-01T00:00:00Z",
        "chat_messages": [
            {"sender": "human", "text": "barrel aging?"},
            {"sender": "assistant", "text": "micro-oxygenation matters"},
        ]}]))
    out = ce.dump_conversations({"last_ts": 0, "last_id": None}, root=str(d))
    assert len(out) == 1
    c = out[0]
    assert c["conv_id"] == "cl1" and c["source"] == "claude"
    assert "barrel aging" in c["text"] and "micro-oxygenation" in c["text"]


def test_dump_adapter_respects_cursor(tmp_path):
    d = tmp_path / "dumps"
    d.mkdir()
    (d / "chatgpt.json").write_text(json.dumps([{
        "id": "cg1", "update_time": 1_000.0,
        "mapping": {"a": {"message": {"author": {"role": "user"},
                                      "content": {"content_type": "text", "parts": ["old"]}}}}}]))
    assert ce.dump_conversations({"last_ts": 2_000, "last_id": None}, root=str(d)) == []


# --------------------------------------------------------------- OWUI adapter

def test_owui_adapter_fetches_new_chats_and_skips_old():
    async def list_fn():
        return [{"id": "c1", "updated_at": 2000}, {"id": "c0", "updated_at": 500}]

    async def chat_fn(cid):
        return {"chat": {"messages": [{"role": "user", "content": "unifi roaming"},
                                      {"role": "assistant", "content": "set DTIM"}]}}

    out = asyncio.run(ce.owui_conversations({"last_ts": 1000, "last_id": None},
                                            list_fn=list_fn, chat_fn=chat_fn))
    assert len(out) == 1                       # c1 (ts 2000) in, c0 (ts 500) below cursor
    assert out[0]["conv_id"] == "c1" and out[0]["source"] == "owui"
    assert "unifi roaming" in out[0]["text"] and "set DTIM" in out[0]["text"]


def test_owui_adapter_normalizes_millisecond_timestamps():
    async def list_fn():
        return [{"id": "c1", "updated_at": 1_700_000_000_000}]   # ms epoch

    async def chat_fn(cid):
        return {"chat": {"messages": [{"role": "user", "content": "hi"}]}}

    out = asyncio.run(ce.owui_conversations({"last_ts": 0, "last_id": None},
                                            list_fn=list_fn, chat_fn=chat_fn))
    assert out[0]["ts"] == 1_700_000_000   # ms collapsed to seconds


# --------------------------------------------------------------- extraction call

def test_extract_parses_structured_output(monkeypatch):
    canned = ('{"nodes":[{"type":"interest","label":"hazelnuts",'
              '"facts":["wants an orchard"],"engagement_signal":1.0,"confidence":0.9}],'
              '"edges":[{"src":"hazelnuts","dst":"food resilience","type":"supports"}],'
              '"threads":[{"question":"best local TTS?","status":"open"}]}')

    async def fake_vera(messages, temperature=0.2):
        return "Here you go: " + canned + " done"

    monkeypatch.setattr(ce, "_vera", fake_vera)
    res = asyncio.run(ce.extract("a conversation about hazelnuts"))
    assert res["nodes"][0]["label"] == "hazelnuts"
    assert res["edges"][0]["type"] == "supports"
    assert res["threads"][0]["status"] == "open"


def test_extract_returns_empty_shape_on_unparseable(monkeypatch):
    async def fake_vera(messages, temperature=0.2):
        return "sorry, no json here"

    monkeypatch.setattr(ce, "_vera", fake_vera)
    assert asyncio.run(ce.extract("x")) == {"nodes": [], "edges": [], "threads": []}


# --------------------------------------------------------------- merge into the graph

EXTRACTED = {
    "nodes": [
        {"type": "interest", "label": "hazelnuts", "facts": ["wants an orchard"], "engagement_signal": 1.0},
        {"type": "project", "label": "food resilience", "facts": [], "engagement_signal": 1.0},
    ],
    "edges": [{"src": "hazelnuts", "dst": "food resilience", "type": "supports"}],
    "threads": [{"question": "best local TTS?", "status": "open"}],
}
CONV = {"conv_id": "c1", "ts": 1_717_000_000, "text": "...", "source": "owui"}


def test_merge_lands_nodes_edges_threads_with_provenance():
    # VERA_EMBED_URL unset in tests → merge falls back to exact-label dedup (deterministic)
    counts = asyncio.run(ce.merge_conversation(CONV, EXTRACTED, now=1_717_000_000))
    nodes = {n["label"]: n for n in pg.all_nodes()}
    assert nodes["hazelnuts"]["type"] == "interest"
    fact = nodes["hazelnuts"]["facts"][0]
    assert fact["text"] == "wants an orchard" and fact["source"] == "extraction:c1"
    assert nodes["food resilience"]["type"] == "project"
    haz, fr = nodes["hazelnuts"]["id"], nodes["food resilience"]["id"]
    assert any(e["dst_id"] == fr and e["type"] == "supports" for e in pg.neighbors(haz))
    thread = nodes["best local TTS?"]
    assert thread["type"] == "thread" and thread["state"] == "open"
    assert counts == {"nodes": 2, "edges": 1, "threads": 1}


def test_merge_resolves_an_open_thread():
    asyncio.run(ce.merge_conversation(CONV, EXTRACTED, now=1_717_000_000))
    later = {"nodes": [], "edges": [],
             "threads": [{"question": "best local TTS?", "status": "resolved"}]}
    asyncio.run(ce.merge_conversation({**CONV, "conv_id": "c2"}, later, now=1_717_100_000))
    thread = {n["label"]: n for n in pg.all_nodes()}["best local TTS?"]
    assert thread["state"] == "resolved"   # the same thread flipped, not duplicated
    assert [n["label"] for n in pg.all_nodes()].count("best local TTS?") == 1


# --------------------------------------------------------------- run() orchestration

def _wire_run(monkeypatch, convs):
    """Wire run() with a cursor-respecting dump source and OWUI empty."""
    monkeypatch.setattr(ce, "dump_conversations",
                        lambda cur, root=None: [c for c in convs if c["ts"] > cur["last_ts"]])

    async def no_owui(cur, **kw):
        return []

    monkeypatch.setattr(ce, "owui_conversations", no_owui)
    monkeypatch.setattr(ce, "OWUI_BASE", "")

    async def fake_extract(text):
        return {"nodes": [{"type": "interest", "label": "hazelnuts", "facts": [],
                           "engagement_signal": 1.0}], "edges": [], "threads": []}

    monkeypatch.setattr(ce, "extract", fake_extract)


def test_run_extracts_merges_and_advances_cursor(monkeypatch):
    convs = [{"conv_id": "c1", "text": "hazelnuts", "ts": 1_717_000_000, "source": "chatgpt"}]
    _wire_run(monkeypatch, convs)
    counts = asyncio.run(ce.run())
    assert counts["conversations"] == 1 and counts["nodes"] == 1
    assert {n["label"] for n in pg.all_nodes()} == {"hazelnuts"}
    from routers import extract_store as es
    assert es.get_cursor("chatgpt")["last_ts"] == 1_717_000_000


def test_run_is_a_noop_when_nothing_is_new(monkeypatch):
    convs = [{"conv_id": "c1", "text": "hazelnuts", "ts": 1_717_000_000, "source": "chatgpt"}]
    _wire_run(monkeypatch, convs)
    asyncio.run(ce.run())                       # first run ingests + advances the cursor
    counts = asyncio.run(ce.run())              # second run: cursor filters everything out
    assert counts["conversations"] == 0
    assert len(pg.all_nodes()) == 1             # no re-merge, no duplication


def test_extraction_job_registered_in_scheduler():
    from routers import scheduler
    assert "conversation_extract" in scheduler.REGISTRY
    label, cron, handler = scheduler.REGISTRY["conversation_extract"]
    assert callable(handler)
