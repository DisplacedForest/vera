import logging
import os
import re
import sqlite3
import uuid

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


def save_image(data: bytes, mime: str = "image/png") -> str | None:
    name = f"{uuid.uuid4().hex}.{_EXT.get(mime, 'png')}"
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


def _absolute(url) -> bool:
    return isinstance(url, str) and url.startswith(("http://", "https://"))


async def _fetch(url: str, token: str | None) -> tuple[bytes, str] | None:
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
    if not data:
        return None
    from .pulse_images import _img_kind
    _, mime = _img_kind(data)
    return data, mime


async def _rehome(url: str, token: str | None) -> str | None:
    got = await _fetch(url, token)
    return save_image(got[0], got[1]) if got else None


@router.post("/pulse/media/migrate", tags=["pulse"])
async def migrate(body: MigrateBody):
    rehomed = missing = changed_cards = 0
    for c in store.list_cards(include_expired=True):
        changed = False
        if _absolute(c.get("image_url")):
            ref = await _rehome(c["image_url"], body.token)
            if ref:
                rehomed += 1
            else:
                missing += 1
            c["image_url"] = ref
            changed = True
        imgs = c.get("inline_images") or []
        for im in imgs:
            if _absolute(im.get("url")):
                ref = await _rehome(im["url"], body.token)
                if ref:
                    rehomed += 1
                    im["url"] = ref
                else:
                    missing += 1
                    im["url"] = ""
                changed = True
        if changed:
            store.rewrite_media(c["id"], c["image_url"], imgs)
            changed_cards += 1
    cleared = _clear_legacy_chat_ids()
    return {"ok": True, "cards_rewritten": changed_cards, "images_rehomed": rehomed,
            "images_missing": missing, "chat_ids_cleared": cleared}


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
