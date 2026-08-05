import asyncio
import inspect


def test_research_module_has_no_open_webui_knowledge_calls():
    from routers import research
    src = inspect.getsource(research)
    assert "OWUI" not in src
    assert "/api/v1/knowledge" not in src
    assert "retrieval/query" not in src


def test_rag_sources_reads_the_document_store(docs_store, fake_embeddings):
    from routers import research
    col = docs_store.create_collection("Library")
    f = docs_store.add_file(col["id"], "notes.txt", b"alpha alpha findings")
    asyncio.run(docs_store.index_file(f["id"]))
    out = asyncio.run(research._rag_sources("alpha"))
    assert len(out) == 1
    assert out[0]["url"] == "local"
    assert "Library" in out[0]["title"] and "notes.txt" in out[0]["title"]
    assert "alpha" in out[0]["content"]


def test_rag_sources_empty_when_unconfigured(docs_store):
    from routers import research
    assert asyncio.run(research._rag_sources("alpha")) == []
