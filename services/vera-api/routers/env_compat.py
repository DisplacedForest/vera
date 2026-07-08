"""Env naming convention + one-release deprecation shims.

The convention (documented in .env.example's header): service endpoints are `*_BASE`,
credentials are `*_KEY`. Names that predate it keep working for one release — `read()`
resolves the canonical name first and falls back to the deprecated one, and the startup
config report calls out any deprecated name still in use so deployments migrate on
their own schedule instead of breaking.
"""

import os

# canonical name -> deprecated fallback (one release, then the fallback goes away)
ALIASES: dict[str, str] = {
    # endpoints -> *_BASE
    "SEARXNG_BASE": "SEARXNG_URL",
    "UNRAID_BASE": "UNRAID_API_URL",
    "HOME_ASSISTANT_BASE": "HOME_ASSISTANT_URL",
    "GROCY_BASE": "GROCY_URL",
    "MEALIE_BASE": "MEALIE_URL",
    "OVERSEERR_BASE": "OVERSEERR_URL",
    # credentials -> *_KEY
    "HOME_ASSISTANT_KEY": "HOME_ASSISTANT_TOKEN",
    "GROCY_KEY": "GROCY_API_KEY",
    "MEALIE_KEY": "MEALIE_API_KEY",
    "OVERSEERR_KEY": "OVERSEERR_API_KEY",
    "WATCH_ORIENTATION": "SIGNALS_ORIENTATION",
    "UNRAID_KEY": "UNRAID_API_KEY",
}


def read(name: str, default: str = "") -> str:
    """The canonical name's value, falling back to its deprecated alias, then `default`."""
    v = os.environ.get(name, "").strip()
    if v:
        return v
    old = ALIASES.get(name)
    if old:
        v = os.environ.get(old, "").strip()
        if v:
            return v
    return default


def is_set(name: str) -> bool:
    return bool(read(name))


def deprecated_in_use() -> list[tuple[str, str]]:
    """(deprecated name, canonical name) pairs the environment is still using —
    the config report prints these as migration nudges."""
    out = []
    for new, old in ALIASES.items():
        if os.environ.get(old, "").strip() and not os.environ.get(new, "").strip():
            out.append((old, new))
    return out
