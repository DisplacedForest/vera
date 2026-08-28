from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import household_store as store

router = APIRouter()


class MemberIn(BaseModel):
    name: str


@router.get("/household", tags=["household"])
async def list_members(include_disabled: bool = False):
    return {"members": store.members(include_disabled=include_disabled)}


@router.post("/household", tags=["household"])
async def add_member(body: MemberIn):
    try:
        return store.add(body.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/household/{member_id}", tags=["household"])
async def rename_member(member_id: str, body: MemberIn):
    try:
        return store.rename(member_id, body.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError:
        raise HTTPException(status_code=404, detail="no such household member")


@router.post("/household/{member_id}/disable", tags=["household"])
async def disable_member(member_id: str):
    try:
        return store.disable(member_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="no such household member")


@router.post("/household/{member_id}/enable", tags=["household"])
async def enable_member(member_id: str):
    try:
        return store.enable(member_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="no such household member")
