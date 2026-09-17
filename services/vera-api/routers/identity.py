import logging
import os

from fastapi import Header, HTTPException

FALLBACK_OWNER_ID = "owner"
USER_HEADER = "X-Vera-User"

log = logging.getLogger("vera.identity")


def _registry_values() -> dict:
    try:
        from . import integrations
        return integrations.integration("owner") or {}
    except Exception:
        return {}


def owner_id() -> str:
    v = _registry_values()
    return ((v.get("user_id") or "").strip()
            or os.environ.get("VERA_OWNER_ID", "").strip()
            or os.environ.get("VERA_DEFAULT_USER", "").strip()
            or FALLBACK_OWNER_ID)


def owner_name() -> str | None:
    v = _registry_values()
    return (v.get("name") or "").strip() or os.environ.get("VERA_OWNER_NAME", "").strip() or None


def owner() -> dict:
    return {"id": owner_id(), "name": owner_name()}


async def active_users() -> list[dict]:
    from . import household_store
    roster = household_store.members(include_disabled=True)
    if not roster:
        return [owner()]
    return [{"id": m["id"], "name": m["name"]} for m in roster if m["enabled"]]


def _member(candidate: str) -> dict:
    from . import household_store
    m = household_store.get(candidate)
    if m is None or not m["enabled"]:
        raise HTTPException(status_code=403, detail="unknown or disabled household member")
    return m


def resolve_user(header: str | None = None, param: str | None = None) -> dict:
    named = (header or "").strip() if isinstance(header, str) else ""
    if named:
        return _member(named)
    legacy = (param or "").strip() if isinstance(param, str) else ""
    if legacy:
        log.info("user_id query parameter used; callers should send the %s header", USER_HEADER)
        return _member(legacy)
    from . import household_store
    seat = household_store.get(owner_id())
    if seat is None:
        return {**owner(), "enabled": True, "created_at": None}
    if not seat["enabled"]:
        raise HTTPException(status_code=403, detail="unknown or disabled household member")
    return seat


async def current_user(
    vera_user: str | None = Header(default=None, alias=USER_HEADER),
    user_id: str | None = None,
) -> dict:
    return resolve_user(vera_user, user_id)
