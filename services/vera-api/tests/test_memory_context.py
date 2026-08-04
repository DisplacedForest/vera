import asyncio
import os

import pytest

from routers import memory_context as mx
from routers import profile_graph_store as pg
from routers import user_profile_store as up


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(pg, "DB_PATH", os.path.join(str(tmp_path), "graph.db"))
    pg.init()
    monkeypatch.setattr(up, "DB_PATH", os.path.join(str(tmp_path), "profiles.db"))
    monkeypatch.delenv("VERA_OWNER_ID", raising=False)
    monkeypatch.delenv("VERA_DEFAULT_USER", raising=False)
    yield


def test_empty_stores_yield_no_memories():
    assert asyncio.run(mx.native_memories()) == []


def test_memories_come_from_profile_and_graph():
    up.set_persona("owner", persona="Keep it terse.")
    nid = pg.upsert_node(type="project", label="vineyard",
                         facts=[pg.make_fact("planted 40 vines", source="test")],
                         engagement=5.0)
    pg.upsert_node(type="thread", label="should I net the vines?",
                   facts=[pg.make_fact("open question", source="test")])
    mems = asyncio.run(mx.native_memories())
    contents = [m["content"] for m in mems]
    assert "Keep it terse." in contents
    assert "vineyard: planted 40 vines" in contents
    assert not any("net the vines" in c for c in contents)
    assert nid


def test_memories_are_scoped_to_the_requested_user():
    up.set_persona("owner", persona="mine")
    up.set_persona("guest", persona="theirs")
    contents = [m["content"] for m in asyncio.run(mx.native_memories("guest"))]
    assert "theirs" in contents and "mine" not in contents


def test_memories_are_capped():
    for i in range(30):
        pg.upsert_node(type="interest", label=f"topic{i}",
                       facts=[pg.make_fact(f"fact {i}", source="test")], engagement=float(i))
    assert len(asyncio.run(mx.native_memories())) == mx.MAX_MEMORIES
