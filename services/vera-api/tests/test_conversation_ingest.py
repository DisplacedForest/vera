import asyncio
import os

import pytest
from fastapi import HTTPException

from routers import conversation_ingest as ci
from routers import conversation_extract as ce
from routers import extract_store as es
from routers import profile_graph_store as pg


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    pg.DB_PATH = os.path.join(str(tmp_path), "graph.db")
    pg.init()
    es.DB_PATH = os.path.join(str(tmp_path), "extract.db")
    es.init()
    monkeypatch.setattr(ci, "AGENT_TOKEN", "sekret")

    async def fake_extract(text):
        return {"nodes": [{"type": "interest", "label": "hazelnuts", "facts": ["roasted are best"],
                           "engagement_signal": 1.0}], "edges": [], "threads": []}

    monkeypatch.setattr(ce, "extract", fake_extract)
    yield


def _body(conv_id="n1", ts=1_750_000_000):
    return ci.IngestBody(conversations=[ci.IngestConversation(
        conv_id=conv_id, ts=ts,
        messages=[ci.IngestMessage(role="user", text="I love hazelnuts"),
                  ci.IngestMessage(role="assistant", text="noted")])])


def test_missing_token_fails_closed():
    with pytest.raises(HTTPException) as e:
        asyncio.run(ci.ingest(_body(), x_agent_token=""))
    assert e.value.status_code == 403


def test_unset_server_token_rejects_everything(monkeypatch):
    monkeypatch.setattr(ci, "AGENT_TOKEN", "")
    with pytest.raises(HTTPException) as e:
        asyncio.run(ci.ingest(_body(), x_agent_token=""))
    assert e.value.status_code == 403


def test_ingest_feeds_the_extract_pipeline():
    out = asyncio.run(ci.ingest(_body(), x_agent_token="sekret"))
    assert out["ingested"] == 1 and out["nodes"] == 1
    nodes = pg.all_nodes()
    assert {n["label"] for n in nodes} == {"hazelnuts"}
    facts = nodes[0]["facts"]
    assert facts and facts[0]["source"] == "extraction:n1"
    assert es.get_cursor("native")["last_ts"] == 1_750_000_000


def test_reposting_the_same_conversation_does_not_duplicate():
    asyncio.run(ci.ingest(_body(), x_agent_token="sekret"))
    out = asyncio.run(ci.ingest(_body(), x_agent_token="sekret"))
    assert out["ingested"] == 0 and out["duplicates"] == 1
    assert len(pg.all_nodes()) == 1
    assert len(pg.all_nodes()[0]["facts"]) == 1


def test_batch_processes_oldest_first_and_skips_empties():
    body = ci.IngestBody(conversations=[
        ci.IngestConversation(conv_id="new", ts=1_750_000_100,
                              messages=[ci.IngestMessage(role="user", text="later")]),
        ci.IngestConversation(conv_id="old", ts=1_750_000_000,
                              messages=[ci.IngestMessage(role="user", text="earlier")]),
        ci.IngestConversation(conv_id="blank", ts=1_750_000_050, messages=[]),
    ])
    out = asyncio.run(ci.ingest(body, x_agent_token="sekret"))
    assert out["ingested"] == 2 and out["empty"] == 1
    assert es.get_cursor("native")["last_ts"] == 1_750_000_100
    assert es.seen("native", "old") and es.seen("native", "new")
    assert not es.seen("native", "blank")


def test_oversize_batch_is_rejected():
    convs = [ci.IngestConversation(conv_id=f"c{i}", ts=i,
                                   messages=[ci.IngestMessage(role="user", text="x")])
             for i in range(ci.MAX_BATCH + 1)]
    with pytest.raises(HTTPException) as e:
        asyncio.run(ci.ingest(ci.IngestBody(conversations=convs), x_agent_token="sekret"))
    assert e.value.status_code == 422


def test_a_failing_conversation_is_isolated(monkeypatch):
    async def boom(text):
        raise RuntimeError("extract died")

    monkeypatch.setattr(ce, "extract", boom)
    out = asyncio.run(ci.ingest(_body(), x_agent_token="sekret"))
    assert out["failed"] == 1 and out["ingested"] == 0
    assert not es.seen("native", "n1")
    out2 = asyncio.run(ci.ingest(_body(), x_agent_token="sekret"))
    assert out2["failed"] == 1
