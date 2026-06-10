"""User-profile router — read/write each person's personal vibe (interests, persona, prefs).

Keyed by OWUI user id. Writes are FREE (about the person, no external effect). The /digest is what
the vera_memory inlet filter injects per-user, layered on the shared world-model core.
"""
from fastapi import APIRouter
from pydantic import BaseModel

from . import user_profile_store as up

router = APIRouter()


class PersonaBody(BaseModel):
    name: str | None = None
    persona: str | None = None
    prefs: dict | None = None


class ObserveBody(BaseModel):
    topic: str
    weight: float = 1.0
    source: str = "vera"
    provenance: dict | None = None
    gloss: str | None = None  # one-line meaning of the interest


class GlossBody(BaseModel):
    topic: str
    gloss: str


@router.get("/profile/{user_id}", tags=["profile"])
async def get_profile(user_id: str):
    return up.get(user_id)


@router.get("/profile/{user_id}/digest", tags=["profile"])
async def get_digest(user_id: str):
    return {"digest": up.digest(user_id)}


@router.post("/profile/{user_id}/persona", tags=["profile"])
async def set_persona(user_id: str, b: PersonaBody):
    up.set_persona(user_id, name=b.name, persona=b.persona, prefs=b.prefs)
    return {"ok": True, "profile": up.get(user_id)}


@router.post("/profile/{user_id}/observe", tags=["profile"])
async def observe(user_id: str, b: ObserveBody):
    iid = up.observe(user_id, b.topic, weight=b.weight, source=b.source, provenance=b.provenance, gloss=b.gloss)
    return {"ok": True, "id": iid}


@router.post("/profile/{user_id}/gloss", tags=["profile"])
async def set_gloss(user_id: str, b: GlossBody):
    """Attach a one-line meaning to an interest so for-you matching is semantic, not lexical."""
    up.set_gloss(user_id, b.topic, b.gloss)
    return {"ok": True}


class ForgetBody(BaseModel):
    id: str


@router.post("/profile/{user_id}/forget", tags=["profile"])
async def forget(user_id: str, b: ForgetBody):
    up.remove_interest(b.id)
    return {"ok": True}
