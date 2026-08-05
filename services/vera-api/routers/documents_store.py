import asyncio
import hashlib
import io
import json
import math
import os
import sqlite3
import time
import uuid
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree

DB_PATH = os.environ.get("DOCUMENTS_DB_PATH", "/data/documents.db")
FILES_DIR = os.environ.get("DOCUMENTS_DIR", "/data/documents")

SUPPORTED_FORMATS = ("txt", "md", "pdf", "docx", "html")


class StorageCapExceeded(Exception):
    pass


class FileTooLarge(Exception):
    pass


def _chunk_chars() -> int:
    return max(200, int(os.environ.get("DOCUMENTS_CHUNK_CHARS", "").strip() or 1200))


def _chunk_overlap() -> int:
    raw = int(os.environ.get("DOCUMENTS_CHUNK_OVERLAP", "").strip() or 200)
    return min(_chunk_chars() // 2, max(0, raw))


def storage_cap() -> int:
    return int(os.environ.get("DOCUMENTS_MAX_BYTES", "").strip() or 1_073_741_824)


def file_cap() -> int:
    return int(os.environ.get("DOCUMENTS_MAX_FILE_BYTES", "").strip() or 67_108_864)


def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS collection (
                id TEXT PRIMARY KEY, name TEXT, description TEXT,
                created_at INTEGER, updated_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS file (
                id TEXT PRIMARY KEY, collection_id TEXT, name TEXT, format TEXT,
                size INTEGER, sha256 TEXT, state TEXT, error TEXT,
                embed_fingerprint TEXT, generation INTEGER, created_at INTEGER,
                updated_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS chunk (
                file_id TEXT, seq INTEGER, text TEXT, vector TEXT,
                PRIMARY KEY (file_id, seq)
            );
            CREATE INDEX IF NOT EXISTS idx_file_collection ON file(collection_id);
            """
        )


def _now() -> int:
    return int(time.time())


def _embeddings_cfg():
    from . import integrations
    return integrations.integration("embeddings")


def embeddings_configured() -> bool:
    cfg = _embeddings_cfg()
    return bool(cfg and cfg.get("url"))


def embed_fingerprint() -> str:
    cfg = _embeddings_cfg() or {}
    raw = f"{cfg.get('url', '')}|{cfg.get('model', '')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


async def _embeddings_post(url: str, payload: dict) -> dict:
    import aiohttp
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=120)) as r:
            r.raise_for_status()
            return await r.json()


def _valid_vector(v, dim: int | None) -> bool:
    if not isinstance(v, list) or not v:
        return False
    if dim is not None and len(v) != dim:
        return False
    return all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
               for x in v)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    cfg = _embeddings_cfg()
    if not cfg or not cfg.get("url"):
        raise RuntimeError("embeddings endpoint is not configured")
    d = await _embeddings_post(f"{cfg['url']}/embeddings",
                               {"model": cfg.get("model", ""), "input": texts})
    rows = sorted(d.get("data") or [], key=lambda x: x.get("index", 0))
    vecs = [r.get("embedding") for r in rows]
    if len(vecs) != len(texts):
        raise ValueError("embeddings endpoint returned a malformed response")
    dim = len(vecs[0]) if isinstance(vecs[0], list) else None
    if any(not _valid_vector(v, dim) for v in vecs):
        raise ValueError("embeddings endpoint returned a malformed response")
    return vecs


class _HTMLText(HTMLParser):
    _SKIP = {"script", "style", "head", "title"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self.parts.append(data.strip())


def _extract_html(data: bytes) -> str:
    p = _HTMLText()
    p.feed(data.decode("utf-8", errors="replace"))
    return "\n".join(p.parts)


def _extract_docx(data: bytes) -> str:
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    paras = []
    for p in root.iter(f"{ns}p"):
        text = "".join(t.text or "" for t in p.iter(f"{ns}t"))
        if text.strip():
            paras.append(text.strip())
    return "\n".join(paras)


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def file_format(name: str) -> str:
    ext = (name.rsplit(".", 1)[-1] if "." in name else "").lower()
    if ext == "htm":
        ext = "html"
    return ext


def extract_text(name: str, data: bytes) -> str:
    fmt = file_format(name)
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"unsupported format '{fmt}' (supported: {', '.join(SUPPORTED_FORMATS)})")
    if fmt in ("txt", "md"):
        text = data.decode("utf-8", errors="replace")
    elif fmt == "html":
        text = _extract_html(data)
    elif fmt == "docx":
        text = _extract_docx(data)
    else:
        text = _extract_pdf(data)
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ValueError("no extractable text")
    return text


def chunk_text(text: str) -> list[str]:
    size = _chunk_chars()
    step = size - _chunk_overlap()
    out = []
    i = 0
    while i < len(text):
        piece = text[i:i + size].strip()
        if piece:
            out.append(piece)
        i += step
    return out


def create_collection(name: str, description: str = "") -> dict:
    init()
    cid = uuid.uuid4().hex
    ts = _now()
    with _conn() as c:
        c.execute("INSERT INTO collection(id, name, description, created_at, updated_at) VALUES (?,?,?,?,?)",
                  (cid, name, description, ts, ts))
    return get_collection(cid)


def _effective_state(state: str, fp: str | None, current_fp: str) -> str:
    if state == "ready" and fp != current_fp:
        return "stale"
    return state


def _aggregate_state(states: list[str]) -> str:
    if not states:
        return "empty"
    if "failed" in states:
        return "failed"
    if "indexing" in states or "pending" in states:
        return "pending"
    if "stale" in states:
        return "stale"
    return "ready"


def _collection_row(c, row) -> dict:
    fp = embed_fingerprint()
    states = [_effective_state(r["state"], r["embed_fingerprint"], fp) for r in
              c.execute("SELECT state, embed_fingerprint FROM file WHERE collection_id=?",
                        (row["id"],))]
    return {"id": row["id"], "name": row["name"], "description": row["description"] or "",
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "file_count": len(states), "index_state": _aggregate_state(states)}


def list_collections() -> list[dict]:
    init()
    with _conn() as c:
        rows = c.execute("SELECT * FROM collection ORDER BY created_at, id").fetchall()
        return [_collection_row(c, r) for r in rows]


def get_collection(cid: str) -> dict | None:
    init()
    with _conn() as c:
        row = c.execute("SELECT * FROM collection WHERE id=?", (cid,)).fetchone()
        return _collection_row(c, row) if row else None


def collection_by_name(name: str) -> dict | None:
    init()
    with _conn() as c:
        row = c.execute("SELECT * FROM collection WHERE name=?", (name,)).fetchone()
        return _collection_row(c, row) if row else None


def update_collection(cid: str, name: str | None = None, description: str | None = None) -> dict | None:
    init()
    with _conn() as c:
        row = c.execute("SELECT id FROM collection WHERE id=?", (cid,)).fetchone()
        if not row:
            return None
        if name is not None:
            c.execute("UPDATE collection SET name=?, updated_at=? WHERE id=?", (name, _now(), cid))
        if description is not None:
            c.execute("UPDATE collection SET description=?, updated_at=? WHERE id=?",
                      (description, _now(), cid))
    return get_collection(cid)


def delete_collection(cid: str) -> bool:
    init()
    with _conn() as c:
        row = c.execute("SELECT id FROM collection WHERE id=?", (cid,)).fetchone()
        if not row:
            return False
        fids = [r["id"] for r in c.execute("SELECT id FROM file WHERE collection_id=?", (cid,))]
        c.execute("DELETE FROM chunk WHERE file_id IN (SELECT id FROM file WHERE collection_id=?)", (cid,))
        c.execute("DELETE FROM file WHERE collection_id=?", (cid,))
        c.execute("DELETE FROM collection WHERE id=?", (cid,))
    for fid in fids:
        _remove_source(fid)
    return True


def _source_path(fid: str) -> str:
    return os.path.join(FILES_DIR, fid)


def _remove_source(fid: str):
    try:
        os.remove(_source_path(fid))
    except OSError:
        pass


def _write_source(fid: str, data: bytes):
    os.makedirs(FILES_DIR, exist_ok=True)
    with open(_source_path(fid), "wb") as f:
        f.write(data)


def read_source(fid: str) -> bytes:
    with open(_source_path(fid), "rb") as f:
        return f.read()


def storage_used() -> int:
    init()
    with _conn() as c:
        row = c.execute("SELECT COALESCE(SUM(size), 0) AS total FROM file").fetchone()
        return int(row["total"])


def _file_row(row) -> dict:
    return {"id": row["id"], "collection_id": row["collection_id"], "name": row["name"],
            "format": row["format"], "size": row["size"], "sha256": row["sha256"],
            "state": _effective_state(row["state"], row["embed_fingerprint"], embed_fingerprint()),
            "error": row["error"],
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


def add_file(cid: str, name: str, data: bytes) -> dict:
    init()
    fmt = file_format(name)
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"unsupported format '{fmt}' (supported: {', '.join(SUPPORTED_FORMATS)})")
    if len(data) > file_cap():
        raise FileTooLarge(
            f"file too large: {len(data)} bytes passes the {file_cap()} byte per-file cap "
            f"(DOCUMENTS_MAX_FILE_BYTES)")
    fid = uuid.uuid4().hex
    ts = _now()
    try:
        with _conn() as c:
            c.execute("BEGIN IMMEDIATE")
            if not c.execute("SELECT id FROM collection WHERE id=?", (cid,)).fetchone():
                raise LookupError("collection not found")
            used = int(c.execute("SELECT COALESCE(SUM(size), 0) AS t FROM file").fetchone()["t"])
            if used + len(data) > storage_cap():
                raise StorageCapExceeded(
                    f"storage cap exceeded: {used + len(data)} bytes would pass the "
                    f"{storage_cap()} byte cap (DOCUMENTS_MAX_BYTES)")
            c.execute(
                "INSERT INTO file(id, collection_id, name, format, size, sha256, state, error, "
                "embed_fingerprint, generation, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (fid, cid, name, fmt, len(data), hashlib.sha256(data).hexdigest(),
                 "pending", None, None, 0, ts, ts))
            _write_source(fid, data)
    except (StorageCapExceeded, LookupError):
        raise
    except Exception:
        _remove_source(fid)
        raise
    return get_file(fid)


def replace_file(fid: str, name: str, data: bytes) -> dict | None:
    init()
    fmt = file_format(name)
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"unsupported format '{fmt}' (supported: {', '.join(SUPPORTED_FORMATS)})")
    if len(data) > file_cap():
        raise FileTooLarge(
            f"file too large: {len(data)} bytes passes the {file_cap()} byte per-file cap "
            f"(DOCUMENTS_MAX_FILE_BYTES)")
    tmp = _source_path(fid) + ".tmp"
    try:
        with _conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM file WHERE id=?", (fid,)).fetchone()
            if not row:
                return None
            used = int(c.execute("SELECT COALESCE(SUM(size), 0) AS t FROM file").fetchone()["t"])
            if used - row["size"] + len(data) > storage_cap():
                raise StorageCapExceeded(
                    f"storage cap exceeded: replacing would pass the {storage_cap()} byte cap "
                    f"(DOCUMENTS_MAX_BYTES)")
            c.execute("DELETE FROM chunk WHERE file_id=?", (fid,))
            c.execute("UPDATE file SET name=?, format=?, size=?, sha256=?, state='stale', "
                      "error=NULL, embed_fingerprint=NULL, generation=generation+1, "
                      "updated_at=? WHERE id=?",
                      (name, fmt, len(data), hashlib.sha256(data).hexdigest(), _now(), fid))
            os.makedirs(FILES_DIR, exist_ok=True)
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, _source_path(fid))
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return get_file(fid)


def delete_file(fid: str) -> bool:
    init()
    with _conn() as c:
        row = c.execute("SELECT id FROM file WHERE id=?", (fid,)).fetchone()
        if not row:
            return False
        c.execute("DELETE FROM chunk WHERE file_id=?", (fid,))
        c.execute("DELETE FROM file WHERE id=?", (fid,))
    _remove_source(fid)
    return True


def get_file(fid: str) -> dict | None:
    init()
    with _conn() as c:
        row = c.execute("SELECT * FROM file WHERE id=?", (fid,)).fetchone()
        return _file_row(row) if row else None


def list_files(cid: str) -> list[dict]:
    init()
    with _conn() as c:
        rows = c.execute("SELECT * FROM file WHERE collection_id=? ORDER BY created_at, id",
                         (cid,)).fetchall()
        return [_file_row(r) for r in rows]


async def index_file(fid: str) -> dict:
    init()
    with _conn() as c:
        row = c.execute("SELECT * FROM file WHERE id=?", (fid,)).fetchone()
    if not row:
        return {"status": "missing"}
    if not embeddings_configured():
        return {"status": "unconfigured",
                "detail": "no embeddings endpoint: enable the Embeddings integration or set "
                          "VERA_EMBED_URL"}
    gen = row["generation"]
    sha = row["sha256"]
    fingerprint = embed_fingerprint()
    stale_claim = int(os.environ.get("DOCUMENTS_INDEX_STALE_SECS", "").strip() or 900)
    with _conn() as c:
        cur = c.execute("UPDATE file SET state='indexing', error=NULL, updated_at=? "
                        "WHERE id=? AND generation=? AND (state != 'indexing' OR updated_at < ?)",
                        (_now(), fid, gen, _now() - stale_claim))
        if cur.rowcount == 0:
            return {"status": "superseded"}
    try:
        data = read_source(fid)
        if hashlib.sha256(data).hexdigest() != sha:
            raise ValueError("source on disk does not match its recorded hash; upload it again")
        text = await asyncio.to_thread(extract_text, row["name"], data)
        chunks = chunk_text(text)
        if not chunks:
            raise ValueError("no extractable text")
        vecs = await embed_texts(chunks)
        with _conn() as c:
            cur = c.execute("UPDATE file SET state='ready', error=NULL, embed_fingerprint=?, "
                            "updated_at=? WHERE id=? AND generation=? AND state='indexing'",
                            (fingerprint, _now(), fid, gen))
            if cur.rowcount == 0:
                return {"status": "superseded"}
            c.execute("DELETE FROM chunk WHERE file_id=?", (fid,))
            c.executemany("INSERT INTO chunk(file_id, seq, text, vector) VALUES (?,?,?,?)",
                          [(fid, i, t, json.dumps(v)) for i, (t, v) in enumerate(zip(chunks, vecs))])
        return {"status": "ready", "chunks": len(chunks)}
    except Exception as e:
        reason = str(e)[:300] or type(e).__name__
        with _conn() as c:
            cur = c.execute("UPDATE file SET state='failed', error=?, updated_at=? "
                            "WHERE id=? AND generation=? AND state='indexing'",
                            (reason, _now(), fid, gen))
            if cur.rowcount == 0:
                return {"status": "superseded"}
        return {"status": "failed", "error": reason}


async def index_collection(cid: str) -> dict:
    init()
    results = {"ready": 0, "failed": 0, "unconfigured": 0}
    for f in list_files(cid):
        r = await index_file(f["id"])
        status = r.get("status", "failed")
        if status == "unconfigured":
            return {"status": "unconfigured", "detail": r.get("detail", "")}
        results[status] = results.get(status, 0) + 1
    return {"status": "ok", **results}


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    score = dot / (na * nb)
    return score if math.isfinite(score) else 0.0


def _top_chunks(qv: list[float], collection_ids: list[str] | None, top_k: int) -> list:
    import heapq
    sql = ("SELECT ch.file_id, ch.seq, ch.text, ch.vector, f.name AS file_name, "
           "f.collection_id, co.name AS collection_name "
           "FROM chunk ch JOIN file f ON f.id = ch.file_id "
           "JOIN collection co ON co.id = f.collection_id "
           "WHERE f.state='ready' AND f.embed_fingerprint=?")
    params: list = [embed_fingerprint()]
    if collection_ids is not None:
        sql += f" AND f.collection_id IN ({','.join('?' * len(collection_ids))})"
        params += list(collection_ids)
    with _conn() as c:
        def scored():
            for r in c.execute(sql, params):
                try:
                    yield (_cosine(qv, json.loads(r["vector"])), dict(r))
                except Exception:
                    continue
        return heapq.nsmallest(max(1, top_k), scored(),
                               key=lambda s: (-s[0], s[1]["file_id"], s[1]["seq"]))


async def query(text: str, collection_ids: list[str] | None = None,
                top_k: int = 8, char_budget: int = 6000) -> dict:
    init()
    if not embeddings_configured():
        return {"status": "unconfigured", "passages": [],
                "detail": "no embeddings endpoint: enable the Embeddings integration or set "
                          "VERA_EMBED_URL"}
    if collection_ids is not None and not collection_ids:
        return {"status": "ok", "passages": []}
    if collection_ids is not None and len(collection_ids) > 100:
        return {"status": "error", "passages": [],
                "detail": "too many collection ids (100 max)"}
    try:
        qv = (await embed_texts([text]))[0]
        top = await asyncio.to_thread(_top_chunks, qv, collection_ids, top_k)
    except Exception as e:
        return {"status": "error", "passages": [], "detail": str(e)[:300]}
    passages = []
    spent = 0
    for score, r in top:
        chunk = r["text"]
        remaining = char_budget - spent
        if remaining <= 0:
            break
        if len(chunk) > remaining:
            if passages:
                break
            chunk = chunk[:remaining]
        passages.append({"collection_id": r["collection_id"], "collection": r["collection_name"],
                         "file_id": r["file_id"], "file": r["file_name"], "chunk": r["seq"],
                         "score": round(score, 4), "text": chunk})
        spent += len(chunk)
    return {"status": "ok", "passages": passages}
