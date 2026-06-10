"""Available stack updates — a System-lane producer.

Like `health`, this is a scheduled producer that folds into the System chip rather than owning its
own lane: when updates are available across the stack it injects ONE `kind=status,
category=update` card (renders under the System detail's Updates group); when everything
is current it clears the card and posts nothing (zero-floor).

Two read-only sources, both already computed upstream:

  1. **Home Assistant** `update.*` **domain** — covers Unraid OS, HA Core/OS/Supervisor + add-ons,
     HACS, and UniFi network gear. `/api/states` gives which entities have an update (`state==on`)
     and the versions; the entity registry (WebSocket) gives each entity's `platform`, which is how
     we filter to infrastructure and group by source (platform is absent from /api/states).
  2. **Unraid container images** — `/var/lib/docker/unraid-update-status.json`, Unraid DockerMan's
     own per-image local-vs-remote digest comparison. vera-api can't read the host file from inside
     its container: mount it read-only (UNRAID_UPDATE_STATUS_PATH), or have any host job POST the
     JSON to this endpoint.

Infra-only by design: per-device IoT firmware (shelly/zha/matter/…) is dropped so the card stays
quiet and meaningful. This producer only reads — applying an update goes through the gated
confirm→apply action staged on each card row.
"""
import asyncio
import json
import os
import uuid

import aiohttp
from fastapi import APIRouter
from pydantic import BaseModel

from . import pulse_store as store
from .pulse import _inject

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

# Card group -> the System lane's monitored-source toggle that governs it.
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


def _signature(components: list[dict]) -> str:
    """Stable, order-independent set of component ids — dedups runs so an unchanged set doesn't
    re-alert, while a changed set (same count, different members) does."""
    return ";".join(sorted(c["id"] for c in components))


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
            out.append(f"**{group}** — {', '.join(names)}")
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


# ---- endpoint ----

class UpdateCheck(BaseModel):
    # The raw contents of Unraid's /var/lib/docker/unraid-update-status.json, posted by the docker
    # host's cron (vera-api can't read the host file from inside its container). Empty = skip containers.
    docker_status: dict = {}


def _active_cards() -> list[dict]:
    return [c for c in store.list_cards()
            if c.get("kind") == "status" and c.get("category") == "update"]


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


@router.post("/updates/check", tags=["updates"])
async def check(req: UpdateCheck):
    """Gather available updates from HA + the container source, and reconcile the single System
    update card. Runs from the built-in scheduler (or any cron). Zero-floor + dedup: unchanged set
    leaves the card in place (no re-incremented unread), a changed set replaces it, nothing pending
    clears it. Each actionable row carries a Confirm-to-apply action."""
    from . import pulse_lanes
    if not pulse_lanes.is_enabled("status"):
        return {"ok": False, "disabled": True, "detail": pulse_lanes.gate_reason("status")}
    sources = pulse_lanes.option_values("status")
    docker_status = req.docker_status
    if not docker_status:
        # Container-update visibility needs the host's update-status JSON. A caller (host cron)
        # can POST it; otherwise read it from an optional read-only mount so the built-in
        # scheduler gets the same data. Absent both, container checks are skipped cleanly.
        path = os.environ.get("UNRAID_UPDATE_STATUS_PATH", "").strip()
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    docker_status = json.load(f)
            except (OSError, ValueError):
                docker_status = {}
    components = _docker_pending(docker_status)
    ha_detail = ""
    if _ha()[1]:
        try:
            states, platforms = await asyncio.gather(_ha_states(), _ha_platforms())
            components += _ha_pending(states, platforms)
        except Exception as e:  # HA unreachable — still report containers; note the gap.
            ha_detail = f"HA unreachable: {str(e)[:70]}"
    # The lane's monitored-source toggles scope what the card carries (all default on).
    components = _scope_components(components, sources)

    signature = _signature(components)
    existing = _active_cards()
    out = {"ok": True, "total": len(components), "posted": False, "cleared": 0, "ha_error": ha_detail}

    if not components:  # everything current — clear any stale card
        for c in existing:
            store.delete_card(c["id"])
            out["cleared"] += 1
        return out

    if any(c.get("change_set") == signature for c in existing):  # dedup: same set
        for c in existing:  # collapse any duplicates, keep the matching one
            if c.get("change_set") != signature:
                store.delete_card(c["id"])
                out["cleared"] += 1
        return out

    for c in existing:  # set changed — replace
        store.delete_card(c["id"])
        out["cleared"] += 1
    n = len(components)
    title = f"{n} update{'s' if n != 1 else ''} available"
    templated = await _templated_names() if any(c["group"] == "Containers" for c in components) else None
    await _inject(title, _summary_body(components), summary=title,
                  items=_build_items(components, templated),
                  kind="status", category="update", severity="notice", change_set=signature)
    out["posted"] = True
    return out
