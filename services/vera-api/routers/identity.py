import os

FALLBACK_OWNER_ID = "owner"


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
    return [owner()]
