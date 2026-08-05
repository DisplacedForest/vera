import asyncio
import io
import zipfile

import pytest


def _docx(text: str) -> bytes:
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


def _pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + o + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(b"trailer\n<< /Size " + str(len(objs) + 1).encode() + b" /Root 1 0 R >>\n"
              b"startxref\n" + str(xref).encode() + b"\n%%EOF")
    return out.getvalue()


def test_extract_each_supported_format(docs_store):
    assert "plain alpha" in docs_store.extract_text("a.txt", b"plain alpha text")
    assert "markdown beta" in docs_store.extract_text("a.md", b"# markdown beta")
    html = b"<html><head><title>skip</title><style>p{}</style></head><body><p>web gamma</p></body></html>"
    out = docs_store.extract_text("a.html", html)
    assert "web gamma" in out and "skip" not in out
    assert "word delta" in docs_store.extract_text("a.docx", _docx("word delta"))
    assert "pdf alpha" in docs_store.extract_text("a.pdf", _pdf("pdf alpha"))


def test_extract_unsupported_and_empty(docs_store):
    with pytest.raises(ValueError, match="unsupported format"):
        docs_store.extract_text("a.exe", b"nope")
    with pytest.raises(ValueError, match="no extractable text"):
        docs_store.extract_text("a.txt", b"   ")


def test_chunking_is_deterministic_with_overlap(docs_store, monkeypatch):
    monkeypatch.setenv("DOCUMENTS_CHUNK_CHARS", "300")
    monkeypatch.setenv("DOCUMENTS_CHUNK_OVERLAP", "50")
    text = "x" * 200 + "OVERLAPMARK" + "y" * 700
    first = docs_store.chunk_text(text)
    second = docs_store.chunk_text(text)
    assert first == second
    assert len(first) > 1
    assert all(len(c) <= 300 for c in first)
    assert first[0][-50:] == first[1][:50]


def test_collection_crud(docs_store):
    col = docs_store.create_collection("Papers", "research pdfs")
    assert col["name"] == "Papers" and col["description"] == "research pdfs"
    assert col["file_count"] == 0 and col["index_state"] == "empty"
    assert col["created_at"] and col["updated_at"]
    assert [c["id"] for c in docs_store.list_collections()] == [col["id"]]
    renamed = docs_store.update_collection(col["id"], name="Articles", description="renamed")
    assert renamed["name"] == "Articles" and renamed["description"] == "renamed"
    assert docs_store.collection_by_name("Articles")["id"] == col["id"]
    assert docs_store.delete_collection(col["id"])
    assert docs_store.get_collection(col["id"]) is None
    assert not docs_store.delete_collection(col["id"])


def test_index_lifecycle_to_ready(docs_store, fake_embeddings):
    col = docs_store.create_collection("Notes")
    f = docs_store.add_file(col["id"], "note.txt", b"alpha alpha alpha notes")
    assert f["state"] == "pending" and f["size"] > 0 and f["sha256"]
    res = asyncio.run(docs_store.index_file(f["id"]))
    assert res == {"status": "ready", "chunks": 1}
    assert docs_store.get_file(f["id"])["state"] == "ready"
    assert docs_store.get_collection(col["id"])["index_state"] == "ready"
    assert docs_store.get_collection(col["id"])["file_count"] == 1


def test_index_failure_carries_reason_and_reindex_recovers(docs_store, fake_embeddings):
    col = docs_store.create_collection("Broken")
    f = docs_store.add_file(col["id"], "bad.pdf", b"not a real pdf")
    res = asyncio.run(docs_store.index_file(f["id"]))
    assert res["status"] == "failed" and res["error"]
    got = docs_store.get_file(f["id"])
    assert got["state"] == "failed" and got["error"]
    assert docs_store.get_collection(col["id"])["index_state"] == "failed"
    docs_store.replace_file(f["id"], "good.txt", b"beta beta recovered")
    assert docs_store.get_file(f["id"])["state"] == "pending"
    res = asyncio.run(docs_store.index_file(f["id"]))
    assert res["status"] == "ready"
    assert docs_store.get_file(f["id"])["state"] == "ready"


def test_unconfigured_management_works_indexing_degrades(docs_store):
    col = docs_store.create_collection("NoEmbed")
    f = docs_store.add_file(col["id"], "a.txt", b"alpha text")
    res = asyncio.run(docs_store.index_file(f["id"]))
    assert res["status"] == "unconfigured" and "VERA_EMBED_URL" in res["detail"]
    assert docs_store.get_file(f["id"])["state"] == "pending"
    q = asyncio.run(docs_store.query("alpha", None))
    assert q["status"] == "unconfigured" and q["passages"] == []


def test_embedding_config_change_marks_stale(docs_store, fake_embeddings):
    col = docs_store.create_collection("Stale")
    f = docs_store.add_file(col["id"], "a.txt", b"alpha content")
    asyncio.run(docs_store.index_file(f["id"]))
    assert docs_store.get_file(f["id"])["state"] == "ready"
    fake_embeddings["cfg"]["model"] = "different-model"
    assert docs_store.get_file(f["id"])["state"] == "stale"
    assert docs_store.get_collection(col["id"])["index_state"] == "stale"
    res = asyncio.run(docs_store.index_file(f["id"]))
    assert res["status"] == "ready"
    assert docs_store.get_file(f["id"])["state"] == "ready"


def test_storage_cap(docs_store, monkeypatch):
    monkeypatch.setenv("DOCUMENTS_MAX_BYTES", "20")
    col = docs_store.create_collection("Capped")
    docs_store.add_file(col["id"], "small.txt", b"0123456789")
    with pytest.raises(docs_store.StorageCapExceeded, match="DOCUMENTS_MAX_BYTES"):
        docs_store.add_file(col["id"], "big.txt", b"0123456789abcdef")
    with pytest.raises(docs_store.StorageCapExceeded):
        files = docs_store.list_files(col["id"])
        docs_store.replace_file(files[0]["id"], "grown.txt", b"0" * 30)


def test_query_ranking_bounds_and_provenance(docs_store, fake_embeddings):
    col = docs_store.create_collection("Rank")
    fa = docs_store.add_file(col["id"], "alpha.txt", b"alpha alpha alpha alpha")
    fb = docs_store.add_file(col["id"], "beta.txt", b"beta beta beta beta")
    asyncio.run(docs_store.index_file(fa["id"]))
    asyncio.run(docs_store.index_file(fb["id"]))
    q = asyncio.run(docs_store.query("alpha", [col["id"]], top_k=8, char_budget=6000))
    assert q["status"] == "ok" and len(q["passages"]) == 2
    scores = [p["score"] for p in q["passages"]]
    assert scores == sorted(scores, reverse=True)
    top = q["passages"][0]
    assert top["file"] == "alpha.txt" and "alpha" in top["text"]
    assert top["collection_id"] == col["id"] and top["collection"] == "Rank"
    assert top["file_id"] == fa["id"] and top["chunk"] == 0
    q1 = asyncio.run(docs_store.query("alpha", [col["id"]], top_k=1))
    assert len(q1["passages"]) == 1 and q1["passages"][0]["file"] == "alpha.txt"
    qb = asyncio.run(docs_store.query("alpha", [col["id"]], top_k=8, char_budget=10))
    assert len(qb["passages"]) == 1 and len(qb["passages"][0]["text"]) == 10


def test_query_empty_is_distinct_from_unconfigured(docs_store, fake_embeddings):
    col = docs_store.create_collection("Empty")
    q = asyncio.run(docs_store.query("alpha", [col["id"]]))
    assert q["status"] == "ok" and q["passages"] == []


def test_query_scopes_to_requested_collections(docs_store, fake_embeddings):
    c1 = docs_store.create_collection("One")
    c2 = docs_store.create_collection("Two")
    f1 = docs_store.add_file(c1["id"], "a.txt", b"alpha here")
    f2 = docs_store.add_file(c2["id"], "b.txt", b"alpha there")
    asyncio.run(docs_store.index_file(f1["id"]))
    asyncio.run(docs_store.index_file(f2["id"]))
    q = asyncio.run(docs_store.query("alpha", [c1["id"]]))
    assert {p["collection_id"] for p in q["passages"]} == {c1["id"]}
    q_all = asyncio.run(docs_store.query("alpha", None))
    assert {p["collection_id"] for p in q_all["passages"]} == {c1["id"], c2["id"]}


def test_delete_file_and_collection_remove_sources(docs_store, fake_embeddings, tmp_path):
    col = docs_store.create_collection("Cleanup")
    f = docs_store.add_file(col["id"], "a.txt", b"alpha")
    path = docs_store._source_path(f["id"])
    import os
    assert os.path.exists(path)
    assert docs_store.delete_file(f["id"])
    assert not os.path.exists(path)
    assert docs_store.get_file(f["id"]) is None
    f2 = docs_store.add_file(col["id"], "b.txt", b"beta")
    path2 = docs_store._source_path(f2["id"])
    docs_store.delete_collection(col["id"])
    assert not os.path.exists(path2)
