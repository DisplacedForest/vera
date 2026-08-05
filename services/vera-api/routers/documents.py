from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from . import documents_store as store

router = APIRouter(prefix="/documents", tags=["documents"])


class CollectionIn(BaseModel):
    name: str
    description: str = ""


class CollectionPatch(BaseModel):
    name: str | None = None
    description: str | None = None


class QueryIn(BaseModel):
    query: str
    collection_ids: list[str] | None = None
    top_k: int = 8
    char_budget: int = 6000


@router.get("/status")
async def status():
    return {"ok": True, "configured": store.embeddings_configured(),
            "storage_used": store.storage_used(), "storage_cap": store.storage_cap(),
            "supported_formats": list(store.SUPPORTED_FORMATS)}


@router.post("/collections")
async def create_collection(body: CollectionIn):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="collection name is required")
    if store.collection_by_name(name):
        raise HTTPException(status_code=409, detail=f"a collection named '{name}' already exists")
    return store.create_collection(name, body.description.strip())


@router.get("/collections")
async def list_collections():
    return {"collections": store.list_collections()}


@router.get("/collections/{cid}")
async def get_collection(cid: str):
    col = store.get_collection(cid)
    if not col:
        raise HTTPException(status_code=404, detail="collection not found")
    return col


@router.patch("/collections/{cid}")
async def update_collection(cid: str, body: CollectionPatch):
    name = body.name.strip() if body.name is not None else None
    if name == "":
        raise HTTPException(status_code=400, detail="collection name cannot be empty")
    if name:
        existing = store.collection_by_name(name)
        if existing and existing["id"] != cid:
            raise HTTPException(status_code=409, detail=f"a collection named '{name}' already exists")
    col = store.update_collection(cid, name, body.description)
    if not col:
        raise HTTPException(status_code=404, detail="collection not found")
    return col


@router.delete("/collections/{cid}")
async def delete_collection(cid: str):
    if not store.delete_collection(cid):
        raise HTTPException(status_code=404, detail="collection not found")
    return {"ok": True}


async def _read_capped(upload: UploadFile, limit: int) -> bytes:
    parts = []
    total = 0
    while True:
        piece = await upload.read(1 << 20)
        if not piece:
            break
        total += len(piece)
        if total > limit:
            raise HTTPException(
                status_code=507,
                detail=f"storage cap exceeded: the upload passes the remaining "
                       f"{limit} byte budget (DOCUMENTS_MAX_BYTES)")
        parts.append(piece)
    return b"".join(parts)


async def _ingest(cid: str, upload: UploadFile, replace_fid: str | None = None) -> dict:
    freed = 0
    if replace_fid:
        existing = store.get_file(replace_fid)
        if not existing:
            raise HTTPException(status_code=404, detail="file not found")
        freed = existing["size"]
    remaining = max(0, store.storage_cap() - store.storage_used() + freed)
    data = await _read_capped(upload, remaining)
    name = upload.filename or "upload"
    try:
        if replace_fid:
            f = store.replace_file(replace_fid, name, data)
            if not f:
                raise HTTPException(status_code=404, detail="file not found")
        else:
            f = store.add_file(cid, name, data)
    except store.StorageCapExceeded as e:
        raise HTTPException(status_code=507, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=415, detail=str(e))
    result = await store.index_file(f["id"])
    return {"file": store.get_file(f["id"]), "index": result}


@router.post("/collections/{cid}/files")
async def upload_file(cid: str, upload: UploadFile = File(...)):
    if not store.get_collection(cid):
        raise HTTPException(status_code=404, detail="collection not found")
    return await _ingest(cid, upload)


@router.get("/collections/{cid}/files")
async def list_files(cid: str):
    if not store.get_collection(cid):
        raise HTTPException(status_code=404, detail="collection not found")
    return {"files": store.list_files(cid)}


@router.post("/collections/{cid}/reindex")
async def reindex_collection(cid: str):
    if not store.get_collection(cid):
        raise HTTPException(status_code=404, detail="collection not found")
    return await store.index_collection(cid)


@router.get("/files/{fid}")
async def get_file(fid: str):
    f = store.get_file(fid)
    if not f:
        raise HTTPException(status_code=404, detail="file not found")
    return f


@router.put("/files/{fid}")
async def replace_file(fid: str, upload: UploadFile = File(...)):
    f = store.get_file(fid)
    if not f:
        raise HTTPException(status_code=404, detail="file not found")
    return await _ingest(f["collection_id"], upload, replace_fid=fid)


@router.delete("/files/{fid}")
async def delete_file(fid: str):
    if not store.delete_file(fid):
        raise HTTPException(status_code=404, detail="file not found")
    return {"ok": True}


@router.post("/files/{fid}/reindex")
async def reindex_file(fid: str):
    if not store.get_file(fid):
        raise HTTPException(status_code=404, detail="file not found")
    return await store.index_file(fid)


@router.post("/query")
async def query(body: QueryIn):
    text = body.query.strip()
    if not text:
        raise HTTPException(status_code=400, detail="query text is required")
    top_k = min(max(1, body.top_k), 50)
    budget = min(max(200, body.char_budget), 60000)
    return await store.query(text, body.collection_ids, top_k=top_k, char_budget=budget)
