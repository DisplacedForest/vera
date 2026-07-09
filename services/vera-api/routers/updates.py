"""Available stack updates — the `stack_updates` vein block for the System vein.

The block emits ONE standing situation for the current pending set (`kind=status,
category=update`, rendered under the System detail's Updates group); the engine
updates the card when the set changes and retires it when everything is current.

Two read-only sources, both already computed upstream:

  1. **Home Assistant** `update.*` **domain** — covers Unraid OS, HA Core/OS/Supervisor + add-ons,
     HACS, and UniFi network gear. `/api/states` gives which entities have an update (`state==on`)
     and the versions; the entity registry (WebSocket) gives each entity's `platform`, which is how
     we filter to infrastructure and group by source (platform is absent from /api/states).
  2. **Unraid container images** — `/var/lib/docker/unraid-update-status.json`, Unraid DockerMan's
     own per-image local-vs-remote digest comparison. vera-api can't read the host file from inside
     its container: mount it read-only (UNRAID_UPDATE_STATUS_PATH).

Infra-only by design: per-device IoT firmware (shelly/zha/matter/…) is dropped so the card stays
quiet and meaningful. The block only reads — applying an update goes through the gated
confirm→apply action staged on each card row.
"""
import asyncio
import json
import os
import uuid

import aiohttp
from fastapi import APIRouter

from . import vein_engine
from . import pulse_store as store

router = APIRouter()

def _ha() -> tuple[str, str]:
    """(url, token) from the integration registry at call time; empty when disabled."""
    from . import integrations
    cfg = integrations.integration("home_assistant") or {}
    return cfg.get("url", ""), cfg.get("token", "")


def _unraid() -> tuple[str, str]:
    """(graphql url, api key) from the registry — for the template lookup; empty when disabled."""
    from . import integrations
    cfg = integrations.integration("unraid") or {}
    return cfg.get("url", ""), cfg.get("api_key", "")

# Infra-only allowlist of HA integration platforms. UniFi gear, HA Core/add-ons (hassio), HACS
# frontend+integrations, and the Plex server app. Everything else — per-device firmware from
# shelly/zha/matter/litterrobot/etc. — is intentionally excluded so the card isn't drowned in
# smart-bulb firmware. An allowlist (not a denylist) means a newly-added IoT integration can't
# silently leak in. The Unraid integration's entities ride in under `hacs`; they're split out
# below by entity-id prefix.
_PLATFORM_GROUP = {
    "unifi": "Network",
    "hassio": "Home Assistant",
    "hacs": "HACS",
    "plex": "Apps",
}

# Render order for the card body. "Containers" comes from the Unraid file; the rest from HA.
GROUP_ORDER = ["Containers", "Unraid OS", "Home Assistant", "HACS", "Network", "Apps"]

# Card group -> the System vein's monitored-source toggle that governs it.
_GROUP_SOURCE = {"Containers": "src_containers", "Unraid OS": "src_host",
                 "Home Assistant": "src_home_assistant", "HACS": "src_home_assistant",
                 "Network": "src_network", "Apps": "src_apps"}


def _scope_components(components: list[dict], sources: dict) -> list[dict]:
    """Drop components whose source toggle is explicitly off (everything defaults on)."""
    return [c for c in components
            if sources.get(_GROUP_SOURCE.get(c["group"], ""), True) is not False]


# ---- pure helpers (unit-tested in tests/test_updates.py) ----

def _friendly_image(image: str) -> str:
    """Friendly container name from an image ref: strip @digest and :tag, take the last path
    segment. `lscr.io/linuxserver/sonarr:latest` -> `sonarr`."""
    ref = image.split("@", 1)[0]
    last = ref.rsplit("/", 1)[-1]
    return last.split(":", 1)[0] or ref


def _docker_pending(status_json: dict) -> list[dict]:
    """Containers with an image update available, from Unraid's update-status cache. DockerMan
    marks each image `"true"` (current), `"false"` (update available), or `"undef"` (couldn't
    check / local-only) — only `"false"` is a real pending update."""
    out = []
    for image, info in (status_json or {}).items():
        if isinstance(info, dict) and info.get("status") == "false":
            out.append({"group": "Containers", "id": f"docker:{image}", "image": image,
                        "name": _friendly_image(image), "cur": None, "latest": None})
    return sorted(out, key=lambda c: c["name"].lower())


def _classify(entity_id: str, platform: str | None) -> str | None:
    """Map an HA update entity to a card group, or None to drop it. The Unraid integration's
    update entities (`update.unraid_*`) come in under the `hacs` platform — pull them into their
    own group rather than burying them in HACS."""
    if entity_id.startswith("update.unraid_"):
        return "Unraid OS"
    return _PLATFORM_GROUP.get(platform or "")


def _ha_label(entity_id: str, attrs: dict) -> str:
    """Display name for an HA update entity: its title if set, else a prettified entity id
    (UniFi/HACS firmware entities often have no title)."""
    title = (attrs or {}).get("title")
    if title:
        return title
    name = entity_id.removeprefix("update.")
    for suffix in ("_update", "_firmware"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.replace("_", " ").strip() or entity_id


def _ha_pending(states: list[dict], platforms: dict[str, str]) -> list[dict]:
    """Available updates from HA: every `update.*` entity that is `on` (update available) and
    passes the infra allowlist, with installed→latest versions."""
    out = []
    for s in states or []:
        eid = s.get("entity_id", "")
        if not eid.startswith("update.") or s.get("state") != "on":
            continue
        group = _classify(eid, platforms.get(eid))
        if group is None:
            continue
        attrs = s.get("attributes") or {}
        out.append({"group": group, "id": eid, "name": _ha_label(eid, attrs),
                    "cur": attrs.get("installed_version"), "latest": attrs.get("latest_version")})
    return out


def _detail(c: dict) -> str:
    """One-line version detail. Versioned components show cur→latest; containers (no semver from a
    digest comparison) just say a new image is available."""
    if c["cur"] and c["latest"]:
        return f"{c['cur']} → {c['latest']}"
    if c["group"] == "Containers":
        return "new image available"
    return ""


def _component_action(c: dict) -> tuple[str, dict] | None:
    """The apply action for a component, or None to render it flag-only. Containers go
    through the Unraid API (`docker.update`); HA `update.*` entities install natively via HA. The
    Unraid OS entity itself is flag-only — applying it is a reboot, not a one-tap card action."""
    if c["group"] == "Containers":
        return "docker.update", {"name": c["name"], "image": c.get("image", "")}
    if c["id"] == "update.unraid_update":
        return None  # Unraid OS proper — reboot, never a button
    if c["id"].startswith("update."):
        return "ha.service", {"domain": "update", "service": "install", "data": {"entity_id": c["id"]}}
    return None


def _summary_body(components: list[dict]) -> str:
    """A short grouped text overview for the card body (the per-row apply affordance lives in
    `items`; this is the at-a-glance fallback)."""
    by_group: dict[str, list[dict]] = {}
    for c in components:
        by_group.setdefault(c["group"], []).append(c)
    out = []
    for group in GROUP_ORDER:
        names = sorted((c["name"] for c in by_group.get(group, [])), key=str.lower)
        if names:
            out.append(f"**{group}** ({', '.join(names)})")
    return "\n\n".join(out)


# ---- HA fetch (I/O) ----

async def _ha_states() -> list[dict]:
    url, token = _ha()
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{url}/api/states", headers={"Authorization": f"Bearer {token}"},
                         timeout=aiohttp.ClientTimeout(total=20)) as r:
            return await r.json()


async def _ha_platforms() -> dict[str, str]:
    """entity_id -> integration platform, from the HA entity registry (WebSocket only). Platform
    is what tells UniFi gear from a smart bulb and HACS from an add-on; /api/states omits it."""
    url, token = _ha()
    ws_url = url.replace("http", "ws", 1) + "/api/websocket"
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=20)) as ws:
            await ws.receive_json()  # auth_required
            await ws.send_json({"type": "auth", "access_token": token})
            await ws.receive_json()  # auth_ok
            await ws.send_json({"id": 1, "type": "config/entity_registry/list"})
            msg = await ws.receive_json()
    return {e["entity_id"]: e.get("platform") for e in msg.get("result", [])}


async def _templated_names() -> set[str] | None:
    """Container names Unraid can actually update — those with a user template. Unraid's
    updater no-ops on compose/manually-created containers, so those rows should be flag-only rather
    than show a doomed button. Returns None if the Unraid API isn't configured/reachable (unknown →
    don't suppress the button), else the set of template-managed names."""
    api_url, api_key = _unraid()
    if not api_url or not api_key:
        return None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(api_url, headers={"x-api-key": api_key},
                              json={"query": "{ docker { containers { names templatePath } } }"},
                              timeout=aiohttp.ClientTimeout(total=15)) as r:
                data = await r.json()
        conts = (((data or {}).get("data") or {}).get("docker") or {}).get("containers") or []
        return {n.lstrip("/") for c in conts if c.get("templatePath") for n in (c.get("names") or [])}
    except Exception:
        return None


def _build_items(components: list[dict], templated: set[str] | None = None) -> list[dict]:
    """Shape each component into a card item, staging an apply action (token) for the actionable
    ones. Flag-only components (Unraid OS, or containers Unraid can't update) get no action
    and render as info rows. Grouped in GROUP_ORDER so the card reads top-down by source."""
    from . import actions  # local import: actions pulls in many routers; avoid load-order coupling

    by_group: dict[str, list[dict]] = {}
    for c in components:
        by_group.setdefault(c["group"], []).append(c)

    items = []
    for group in GROUP_ORDER:
        for c in sorted(by_group.get(group, []), key=lambda c: c["name"].lower()):
            item = {"item_id": str(uuid.uuid4()), "group": group,
                    "title": c["name"], "subtitle": _detail(c), "state": "info"}
            va = _component_action(c)
            # Suppress the container button when Unraid has no template for it (would no-op).
            if va and va[0] == "docker.update" and templated is not None and c["name"] not in templated:
                va = None
            if va:
                ok, err = actions._stage(va[0], va[1], "scheduled", "vera", None, None)
                if not err:
                    item["action"] = {"verb": va[0], "args": va[1], "token": ok["token"],
                                      "preview": ok["preview"], "risk": ok["risk"],
                                      "reversible": ok["reversible"]}
                    item["state"] = "pending"
            items.append(item)
    return items


def _read_status_file() -> dict:
    path = os.environ.get("UNRAID_UPDATE_STATUS_PATH", "").strip()
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


async def _gather_components(sources: dict) -> list[dict]:
    components = _docker_pending(_read_status_file())
    if _ha()[1]:
        try:
            states, platforms = await asyncio.gather(_ha_states(), _ha_platforms())
            components += _ha_pending(states, platforms)
        except Exception:
            pass
    return _scope_components(components, sources)


async def _block_stack_updates(items, params, ctx):
    components = await _gather_components(ctx.get("options") or {})
    if not components:
        return items
    n = len(components)
    item = {"key": "updates", "title": f"{n} update{'s' if n != 1 else ''} available",
            "content": _summary_body(components), "severity": "notice", "category": "update"}
    active = [c for c in store.list_cards()
              if c.get("kind") == ctx.get("kind") and c.get("situation_key") == "updates"
              and c.get("status") in ("new", "seen")]
    if not any(c.get("change_set") == vein_engine._content_sig(item) for c in active):
        templated = (await _templated_names()
                     if any(c["group"] == "Containers" for c in components) else None)
        item["items"] = _build_items(components, templated)
    return items + [item]


vein_engine.register("stack_updates", _block_stack_updates, monitor=True,
                     describe="emits one standing item summarizing pending component updates from the connected integrations, with per row apply actions")
