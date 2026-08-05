import base64
import logging
import os
import re
import sqlite3
import uuid

from urllib.parse import urlsplit

import aiohttp
from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import pulse_store as store

log = logging.getLogger("vera.pulse")

router = APIRouter()

_NAME_RE = re.compile(r"^[a-f0-9]{32}\.(png|jpg|gif|webp)$")
_MIME = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
_EXT = {v: k for k, v in _MIME.items()}


def media_dir() -> str:
    return os.environ.get("PULSE_MEDIA_DIR", "/data/pulse/media")


def sniff_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def save_image(data: bytes) -> str | None:
    mime = sniff_mime(data)
    if not mime:
        log.error("pulse media save rejected a non-image payload (%d bytes)", len(data))
        return None
    name = f"{uuid.uuid4().hex}.{_EXT[mime]}"
    try:
        d = media_dir()
        os.makedirs(d, exist_ok=True)
        tmp = os.path.join(d, f".{name}.tmp")
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, os.path.join(d, name))
    except OSError as e:
        log.error("pulse media save failed for %s: %s", name, e)
        return None
    return f"/pulse/media/{name}"


def as_data_uri(ref: str) -> str | None:
    name = ref.rsplit("/", 1)[-1]
    if not _NAME_RE.match(name):
        return None
    try:
        with open(os.path.join(media_dir(), name), "rb") as f:
            data = f.read()
    except OSError:
        return None
    return f"data:{_MIME[name.rsplit('.', 1)[1]]};base64,{base64.b64encode(data).decode()}"


@router.get("/pulse/media/{name}", tags=["pulse"])
async def serve(name: str):
    if not _NAME_RE.match(name):
        return JSONResponse({"error": "not found"}, status_code=404)
    path = os.path.join(media_dir(), name)
    if not os.path.isfile(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path, media_type=_MIME[name.rsplit(".", 1)[1]])


class MigrateBody(BaseModel):
    token: str | None = None
    source_base: str | None = None


def _absolute(url) -> bool:
    return isinstance(url, str) and url.startswith(("http://", "https://"))


def _origin(url: str) -> tuple | None:
    try:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return None
        port = parts.port or {"http": 80, "https": 443}[parts.scheme]
    except ValueError:
        return None
    return parts.scheme, parts.hostname, port


def _token_for(url: str, body: MigrateBody) -> str | None:
    if not (body.token and body.source_base):
        return None
    want, got = _origin(body.source_base), _origin(url)
    if not want or not got or want != got:
        return None
    return body.token


async def _fetch(url: str, token: str | None) -> bytes | None:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=headers,
                             timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status != 200:
                    return None
                data = await r.read()
    except Exception:
        return None
    return data if data and sniff_mime(data) else None


async def _rehome(url: str, token: str | None) -> tuple[str, str | None]:
    data = await _fetch(url, token)
    if not data:
        return "missing", None
    ref = save_image(data)
    if not ref:
        return "error", None
    return "rehomed", ref


@router.post("/pulse/media/migrate", tags=["pulse"])
async def migrate(body: MigrateBody):
    rehomed = missing = errors = changed_cards = 0
    for c in store.list_cards(include_expired=True):
        changed = False
        if _absolute(c.get("image_url")):
            status, ref = await _rehome(c["image_url"], _token_for(c["image_url"], body))
            if status == "rehomed":
                rehomed += 1
                c["image_url"] = ref
                changed = True
            elif status == "missing":
                missing += 1
                c["image_url"] = None
                changed = True
            else:
                errors += 1
        imgs = c.get("inline_images") or []
        for im in imgs:
            if _absolute(im.get("url")):
                status, ref = await _rehome(im["url"], _token_for(im["url"], body))
                if status == "rehomed":
                    rehomed += 1
                    im["url"] = ref
                    changed = True
                elif status == "missing":
                    missing += 1
                    im["url"] = ""
                    changed = True
                else:
                    errors += 1
        if changed:
            store.rewrite_media(c["id"], c["image_url"], imgs)
            changed_cards += 1
    cleared = _clear_legacy_chat_ids()
    return {"ok": True, "cards_rewritten": changed_cards, "images_rehomed": rehomed,
            "images_missing": missing, "errors": errors, "chat_ids_cleared": cleared}


def _clear_legacy_chat_ids() -> int:
    store.init()
    with store._conn() as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(cards)").fetchall()]
        if "promoted_chat_id" not in cols:
            return 0
        try:
            return c.execute(
                "UPDATE cards SET promoted_chat_id=NULL WHERE promoted_chat_id IS NOT NULL"
            ).rowcount
        except sqlite3.OperationalError:
            return 0
