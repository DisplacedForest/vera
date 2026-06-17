"""vera_memory core-tier discipline — the model-facing /memory/self/write cannot land a write
directly in the always-injected `core` tier; a requested core write is redirected to `archive`
and reaches core only through the groomer's promotion. scratch/archive stay free. Run under
pytest."""
import asyncio
import os

import pytest

from routers import vera_memory as vmr
from routers import vera_memory_store as vm


@pytest.fixture(autouse=True)
def _fresh(tmp_path):
    vm.DB_PATH = os.path.join(str(tmp_path), "store.db")
    vm.init()
    yield


def _write(**kw):
    return asyncio.run(vmr.self_write(vmr.WriteBody(**kw)))


def test_core_write_is_redirected_to_archive():
    res = _write(topic="world", content="something she learned", tier="core")
    assert res["tier"] == "archive"
    assert "note" in res                                   # tells the model why
    assert vm.get(res["id"])["tier"] == "archive"          # never lands in core


def test_scratch_and_archive_writes_unchanged_and_free():
    s = _write(topic="a", content="scribble", tier="scratch")
    assert vm.get(s["id"])["tier"] == "scratch"
    a = _write(topic="b", content="note", tier="archive")
    assert vm.get(a["id"])["tier"] == "archive"


def test_groomer_promotion_still_reaches_core():
    a = _write(topic="durable", content="a grounded, lasting belief", tier="archive")
    vm.set_tier(a["id"], "core")                           # the groomer's internal promotion path
    assert vm.get(a["id"])["tier"] == "core"               # core stays reachable, just not per-write
