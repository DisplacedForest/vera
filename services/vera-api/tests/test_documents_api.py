import asyncio
import io

import pytest
from fastapi import HTTPException, UploadFile

from routers import documents


def _upload(name: str, data: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=name)


def test_collection_endpoints(docs_store):
    col = asyncio.run(documents.create_collection(documents.CollectionIn(name="Docs", description="d")))
    assert col["name"] == "Docs"
    with pytest.raises(HTTPException) as e:
        asyncio.run(documents.create_collection(documents.CollectionIn(name="Docs")))
    assert e.value.status_code == 409
    with pytest.raises(HTTPException) as e:
        asyncio.run(documents.create_collection(documents.CollectionIn(name="  ")))
    assert e.value.status_code == 400
    got = asyncio.run(documents.get_collection(col["id"]))
    assert got["id"] == col["id"]
    patched = asyncio.run(documents.update_collection(
        col["id"], documents.CollectionPatch(description="newdesc")))
    assert patched["description"] == "newdesc" and patched["name"] == "Docs"
    listing = asyncio.run(documents.list_collections())
    assert len(listing["collections"]) == 1
    assert asyncio.run(documents.delete_collection(col["id"])) == {"ok": True}
    with pytest.raises(HTTPException) as e:
        asyncio.run(documents.get_collection(col["id"]))
    assert e.value.status_code == 404


def test_upload_index_and_file_endpoints(docs_store, fake_embeddings):
    col = asyncio.run(documents.create_collection(documents.CollectionIn(name="Up")))
    res = asyncio.run(documents.upload_file(col["id"], _upload("a.txt", b"alpha alpha")))
    assert res["file"]["state"] == "ready" and res["index"]["status"] == "ready"
    fid = res["file"]["id"]
    files = asyncio.run(documents.list_files(col["id"]))["files"]
    assert [f["id"] for f in files] == [fid]
    assert files[0]["name"] == "a.txt" and files[0]["size"] == len(b"alpha alpha")
    rep = asyncio.run(documents.replace_file(fid, _upload("b.txt", b"beta beta")))
    assert rep["file"]["name"] == "b.txt" and rep["file"]["state"] == "ready"
    re_res = asyncio.run(documents.reindex_file(fid))
    assert re_res["status"] == "ready"
    col_re = asyncio.run(documents.reindex_collection(col["id"]))
    assert col_re["status"] == "ok" and col_re["ready"] == 1
    assert asyncio.run(documents.delete_file(fid)) == {"ok": True}
    with pytest.raises(HTTPException) as e:
        asyncio.run(documents.get_file(fid))
    assert e.value.status_code == 404


def test_upload_error_shapes(docs_store, fake_embeddings, monkeypatch):
    col = asyncio.run(documents.create_collection(documents.CollectionIn(name="Err")))
    with pytest.raises(HTTPException) as e:
        asyncio.run(documents.upload_file(col["id"], _upload("a.exe", b"x")))
    assert e.value.status_code == 415
    monkeypatch.setenv("DOCUMENTS_MAX_BYTES", "3")
    with pytest.raises(HTTPException) as e:
        asyncio.run(documents.upload_file(col["id"], _upload("a.txt", b"too big")))
    assert e.value.status_code == 507
    with pytest.raises(HTTPException) as e:
        asyncio.run(documents.upload_file("missing", _upload("a.txt", b"x")))
    assert e.value.status_code == 404


def test_upload_of_unparseable_file_reports_failed(docs_store, fake_embeddings):
    col = asyncio.run(documents.create_collection(documents.CollectionIn(name="Bad")))
    res = asyncio.run(documents.upload_file(col["id"], _upload("bad.pdf", b"garbage")))
    assert res["file"]["state"] == "failed"
    assert res["index"]["status"] == "failed" and res["index"]["error"]


def test_query_endpoint_bounds_and_states(docs_store, fake_embeddings):
    col = asyncio.run(documents.create_collection(documents.CollectionIn(name="Q")))
    asyncio.run(documents.upload_file(col["id"], _upload("a.txt", b"alpha alpha")))
    q = asyncio.run(documents.query(documents.QueryIn(query="alpha", top_k=500, char_budget=5)))
    assert q["status"] == "ok" and len(q["passages"]) >= 1
    with pytest.raises(HTTPException) as e:
        asyncio.run(documents.query(documents.QueryIn(query="  ")))
    assert e.value.status_code == 400


def test_status_endpoint(docs_store):
    st = asyncio.run(documents.status())
    assert st["ok"] is True and st["configured"] is False
    assert st["storage_used"] == 0 and st["storage_cap"] > 0
    assert "pdf" in st["supported_formats"]
