"""Integration registry — every connected service is a declared, gated plugin.

One place describes each service (fields, what it unlocks, how to test it, any
experimental features and their ramifications); the API exposes that description plus
live status so a client can render a plugin store without hardcoding anything.
Capability code consults `integration(id)` / `feature_enabled(id, feature)` at call
time instead of reading raw env vars, so enabling or disabling an integration takes
effect immediately — no restart.

  GET  /integrations            -> every integration with status + field/feature state
  PUT  /integrations/{id}       -> set fields, enable/disable, toggle features
  POST /integrations/{id}/test  -> live connection check (nothing persisted)

Resolution per field: env wins (an env-set field is locked against runtime edits —
same contract as the scheduler), the store carries runtime edits. An integration is
enabled when its required fields are present AND its enabled flag allows it; with no
stored flag, configured means enabled, so an env-configured deployment keeps working
with zero migration. Experimental features are different: always OFF until explicitly
enabled with `ack: true` — the consent is persisted, and the parent integration must
be enabled first. Nothing optional ever turns itself on.
"""

import os
import time

import aiohttp
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import env_compat
from . import integrations_store as store

router = APIRouter()

_TIMEOUT = aiohttp.ClientTimeout(total=10)


# ---- registry: integrations as data ----------------------------------------------------

REGISTRY: dict[str, dict] = {
    "coder": {
        "display_name": "Coder / Dream model",
        "fields": [
            {"id": "url", "env": "DREAM_BASE", "label": "OpenAI-compatible base URL", "secret": False,
             "hint": "any /v1 endpoint (llama.cpp, vLLM, llama-swap, mlx_lm.server, or a hosted API)"},
            {"id": "model", "env": "DREAM_MODEL", "label": "Model id", "secret": False},
            {"id": "tool_protocol", "env": "DREAM_TOOL_PROTOCOL", "label": "Tool-call protocol",
             "secret": False, "optional": True, "choices": ["openai", "hermes"],
             "hint": "openai = standard tool_calls (default); hermes = Hermes-style text tool "
                     "calls for servers that don't emit OpenAI tool_calls (mlx_lm.server and "
                     "similar); the value mlx is a deprecated alias for hermes"},
        ],
        "unlocks": ["nightly dreaming consolidation and grooming",
                    "fact verification research with web search"],
    },
    "image_gen": {
        "display_name": "Image generation",
        "fields": [
            {"id": "url", "env": "VERA_IMAGE_BASE", "label": "Base URL", "secret": False,
             "hint": "any endpoint serving the OpenAI Images API (POST /v1/images/generations). "
                     "services/vera-image works out of the box"},
            {"id": "protocol", "env": "IMAGE_PROTOCOL", "label": "Protocol",
             "secret": False, "optional": True, "choices": ["openai", "vera"],
             "hint": "openai = standard Images API (default); vera = the bespoke reference "
                     "contract (deterministic seeds + the vision pause/resume extension)"},
        ],
        "unlocks": ["cover art on Pulse briefing cards"],
    },
    "home_assistant": {
        "display_name": "Home Assistant",
        "fields": [
            {"id": "url", "env": "HOME_ASSISTANT_BASE", "label": "Base URL", "secret": False,
             "hint": "use an IP, not .local. Containers on a bridge network can't resolve mDNS"},
            {"id": "token", "env": "HOME_ASSISTANT_KEY", "label": "Long-lived access token", "secret": True},
        ],
        "unlocks": ["live home state in chat, heartbeat, and cards",
                    "confirm-gated device actuation",
                    "home map reconciliation against live entities"],
        "features": [
            {"id": "home_modeling", "label": "Home modeling",
             "ramifications": (
                 "Captures every Home Assistant state change house-wide (roughly 5,000–15,000 "
                 "events per day on a 30-day rolling window) and models the household's rhythm "
                 "from 10–90 days of accumulation. Adds nightly model, reconcile, and digest jobs. "
                 "Experimental: the miners are unvalidated at scale.")},
        ],
    },
    "grocy": {
        "display_name": "Grocy",
        "fields": [
            {"id": "url", "env": "GROCY_BASE", "label": "Base URL", "secret": False},
            {"id": "api_key", "env": "GROCY_KEY", "label": "API key", "secret": True},
        ],
        "unlocks": ["kitchen inventory and expiry tracking", "shopping list", "stock adjustments from chat"],
        "paired_with": {"id": "mealie", "label": "recipe suggestions from expiring inventory"},
    },
    "mealie": {
        "display_name": "Mealie",
        "fields": [
            {"id": "url", "env": "MEALIE_BASE", "label": "Base URL", "secret": False},
            {"id": "api_key", "env": "MEALIE_KEY", "label": "API token", "secret": True},
        ],
        "unlocks": ["recipe import and browse", "recipe classification"],
        "paired_with": {"id": "grocy", "label": "recipe suggestions from expiring inventory"},
    },
    "overseerr": {
        "display_name": "Overseerr",
        "fields": [
            {"id": "url", "env": "OVERSEERR_BASE", "label": "Base URL", "secret": False},
            {"id": "api_key", "env": "OVERSEERR_KEY", "label": "API key", "secret": True},
        ],
        "unlocks": ["media requests from chat", "library availability checks"],
        "features": [
            {"id": "media_curation", "label": "Media curation digest",
             "ramifications": (
                 "Adds a weekly job that sweeps discovery sources through Overseerr, runs an LLM "
                 "taste pass over the pool, and posts a worth-adding digest card. Experimental: "
                 "it has run exactly once at scale. Expect rough edges in selection quality.")},
        ],
    },
    "unraid": {
        "display_name": "Unraid",
        "fields": [
            {"id": "url", "env": "UNRAID_BASE", "label": "GraphQL endpoint", "secret": False},
            {"id": "api_key", "env": "UNRAID_KEY", "label": "API key", "secret": True},
        ],
        "unlocks": ["confirm-gated container updates and host actuation"],
    },
    "searxng": {
        "display_name": "SearXNG",
        "fields": [
            {"id": "url", "env": "SEARXNG_BASE", "label": "Search endpoint", "secret": False,
             "hint": "the /search endpoint of your SearXNG instance"},
        ],
        "unlocks": ["web search for chat, research, Pulse, and signals news"],
    },
    "embeddings": {
        "display_name": "Embeddings",
        "fields": [
            {"id": "url", "env": "VERA_EMBED_URL", "label": "OpenAI-compatible /v1 base URL", "secret": False,
             "hint": "any /v1 endpoint serving POST /v1/embeddings (llama.cpp, vLLM, llama-swap, "
                     "or a hosted API)"},
            {"id": "model", "env": "VERA_EMBED_MODEL", "label": "Embedding model id", "secret": False,
             "optional": True, "hint": "required by multi-model servers (llama-swap, hosted APIs); "
                                       "single-model servers ignore it"},
        ],
        "unlocks": ["Pulse novelty ranking and the duplicate-finding floor",
                    "profile-graph node embeddings for dedup-merge"],
    },
    "reddit": {
        "display_name": "Reddit",
        "fields": [
            {"id": "client_id", "env": "REDDIT_CLIENT_ID", "label": "App client ID", "secret": True,
             "hint": "create a 'script' app at reddit.com/prefs/apps; the id sits under the app name"},
            {"id": "client_secret", "env": "REDDIT_CLIENT_SECRET", "label": "App secret", "secret": True},
            {"id": "user_agent", "env": "REDDIT_USER_AGENT", "label": "User-Agent", "secret": False,
             "optional": True, "hint": "a descriptive UA, e.g. vera-scout/1.0 by /u/you"},
        ],
        "unlocks": ["Reddit as a Pulse research source (reddit-native search via the official API)"],
    },
    "apple_reminders": {
        "display_name": "Apple Reminders",
        "fields": [
            {"id": "url", "env": "VERA_REMINDERS_URL", "label": "Bridge URL", "secret": False,
             "hint": "the vera-reminders bridge on a Mac signed into iCloud "
                     "(services/vera-reminders, default port 8132)"},
        ],
        "unlocks": ["read and write Reminders lists from chat, shared lists included"],
    },
}

# Legacy kill-switches: these env vars can force a feature OFF (back-compat with
# pre-registry deployments) but can never turn one on — consent always comes first.
_FEATURE_KILL_SWITCH = {
    ("home_assistant", "home_modeling"): "HOME_EVENTS_ENABLED",
    ("overseerr", "media_curation"): "MEDIA_CURATION_ENABLED",
}

# Last live-test outcome per integration, in memory only (a probe result is a moment
# in time, not config). {iid: {"ok": bool, "detail": str, "ts": float}}
_last_test: dict[str, dict] = {}


# ---- resolution (env wins per field; store carries runtime edits) ----------------------

def _field_value(iid: str, f: dict, doc: dict | None = None) -> tuple[str, bool]:
    """(effective value, env_locked) for one field. Canonical env name first, then its
    deprecated alias (one-release migration shim) — either pins the field."""
    env_v = env_compat.read(f["env"])
    if env_v:
        return env_v, True
    doc = doc if doc is not None else store.load()
    stored = ((doc.get(iid) or {}).get("fields") or {}).get(f["id"], "")
    return str(stored or "").strip(), False


def _resolved(iid: str, doc: dict | None = None) -> dict:
    """Effective field values + configured/enabled for one integration."""
    doc = doc if doc is not None else store.load()
    spec = REGISTRY[iid]
    values, locked = {}, {}
    for f in spec["fields"]:
        v, is_env = _field_value(iid, f, doc)
        values[f["id"]] = v
        locked[f["id"]] = is_env
    configured = all(values[f["id"]] for f in spec["fields"] if not f.get("optional"))
    flag = (doc.get(iid) or {}).get("enabled")
    enabled = configured and (flag if flag is not None else True)
    return {"values": values, "env_locked": locked, "configured": configured, "enabled": enabled}


def _feature_state(iid: str, fid: str, doc: dict | None = None) -> dict:
    doc = doc if doc is not None else store.load()
    st = (((doc.get(iid) or {}).get("features") or {}).get(fid) or {})
    acked = bool(st.get("acked_at"))
    enabled = bool(st.get("enabled")) and acked
    kill = _FEATURE_KILL_SWITCH.get((iid, fid))
    if kill and os.environ.get(kill, "").strip().lower() == "false":
        enabled = False
    return {"enabled": enabled, "acked": acked}


def integration(iid: str) -> dict | None:
    """Effective field values (urls slash-trimmed) when the integration is enabled,
    else None. THE accessor capability code uses instead of raw env reads."""
    if iid not in REGISTRY:
        return None
    r = _resolved(iid)
    if not r["enabled"]:
        return None
    out = dict(r["values"])
    for k, v in out.items():
        if k == "url" or k.endswith("_url"):
            out[k] = v.rstrip("/")
    return out


def feature_enabled(iid: str, fid: str) -> bool:
    """True only when the parent integration is enabled AND the experimental feature
    was explicitly enabled with its ramifications acknowledged."""
    if iid not in REGISTRY:
        return False
    doc = store.load()
    if not _resolved(iid, doc)["enabled"]:
        return False
    return _feature_state(iid, fid, doc)["enabled"]


def disabled_detail(iid: str) -> str:
    """One-line 503 detail for endpoints whose integration is off."""
    spec = REGISTRY.get(iid, {})
    env_names = "/".join(f["env"] for f in spec.get("fields", []))
    return (f"{spec.get('display_name', iid)} is not enabled. "
            f"Enable it in the plugin store or set {env_names}")


# ---- views ------------------------------------------------------------------------------

def _entry(iid: str, doc: dict) -> dict:
    spec = REGISTRY[iid]
    r = _resolved(iid, doc)
    fields = []
    for f in spec["fields"]:
        item = {"id": f["id"], "label": f["label"], "secret": f["secret"],
                "env": f["env"], "env_locked": r["env_locked"][f["id"]]}
        if f.get("hint"):
            item["hint"] = f["hint"]
        if f.get("optional"):
            item["optional"] = True
        if f.get("choices"):
            item["choices"] = f["choices"]
        if f["secret"]:
            item["set"] = bool(r["values"][f["id"]])
        else:
            item["value"] = r["values"][f["id"]]
        fields.append(item)
    features = []
    for ft in spec.get("features", []):
        features.append({"id": ft["id"], "label": ft["label"], "experimental": True,
                         "ramifications": ft["ramifications"],
                         **_feature_state(iid, ft["id"], doc)})
    status = "enabled" if r["enabled"] else ("configured" if r["configured"] else "unconfigured")
    test = _last_test.get(iid)
    if test and not test["ok"] and r["enabled"]:
        status = "error"
    entry = {"id": iid, "display_name": spec["display_name"], "status": status,
             "enabled": r["enabled"], "configured": r["configured"],
             "unlocks": spec["unlocks"], "fields": fields, "features": features,
             "last_test": test}
    pw = spec.get("paired_with")
    if pw:
        entry["paired_with"] = {**pw, "active": r["enabled"] and _resolved(pw["id"], doc)["enabled"]}
    return entry


def report_lines() -> list[str]:
    """Startup config-report block: one line per integration."""
    doc = store.load()
    lines = []
    for iid, spec in REGISTRY.items():
        r = _resolved(iid, doc)
        missing = [f["env"] for f in spec["fields"]
                   if not f.get("optional") and not r["values"][f["id"]]]
        state = "enabled" if r["enabled"] else ("configured, disabled" if r["configured"] else
                                               f"unconfigured (missing {', '.join(missing)})")
        feats = "  ".join(
            f"{ft['id']}={'on' if _feature_state(iid, ft['id'], doc)['enabled'] else 'off'}"
            for ft in spec.get("features", []))
        lines.append(f"  [{iid:<14}] {state}" + (f"  {feats}" if feats else ""))
    return lines


# ---- live connection probes -------------------------------------------------------------

async def _probe(iid: str, v: dict) -> dict:
    """One round-trip against the integration's API; ok/detail, never raises."""
    url = v.get("url", "").rstrip("/")
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
            if iid == "coder":
                async with s.get(f"{url}/models") as r:
                    body = await r.json(content_type=None)
                    ids = [m.get("id") for m in ((body or {}).get("data") or []) if isinstance(m, dict)]
                    if r.status != 200:
                        return {"ok": False, "detail": f"HTTP {r.status}"}
                    want = (v.get("model") or "").strip()
                    if want and ids and want not in ids:
                        return {"ok": True, "detail": f"endpoint up, but '{want}' is not in its model list"}
                    return {"ok": True,
                            "detail": f"endpoint up ({len(ids)} model{'s' if len(ids) != 1 else ''})" if ids
                            else "endpoint up"}
            if iid == "image_gen":
                # Cheap reachability only — a real Images call costs a full generation. The
                # vera reference service answers /health; for a generic OpenAI endpoint any
                # HTTP answer counts (404 there just means "not the vera service").
                async with s.get(f"{url}/health") as r:
                    if r.status == 200:
                        body = await r.json(content_type=None)
                        ok = bool((body or {}).get("ok"))
                        return {"ok": ok, "detail": "image service up" if ok
                                else "endpoint answered /health without ok"}
                    return {"ok": True, "detail": f"endpoint reachable (HTTP {r.status} on /health)"}
            if iid == "home_assistant":
                async with s.get(f"{url}/api/", headers={"Authorization": f"Bearer {v.get('token', '')}"}) as r:
                    body = await r.json(content_type=None)
                    ok = r.status == 200 and isinstance(body, dict) and "message" in body
                    return {"ok": ok, "detail": body.get("message", f"HTTP {r.status}") if isinstance(body, dict) else f"HTTP {r.status}"}
            if iid == "grocy":
                async with s.get(f"{url}/api/system/info", headers={"GROCY-API-KEY": v.get("api_key", "")}) as r:
                    body = await r.json(content_type=None)
                    ver = (body or {}).get("grocy_version", {})
                    ok = r.status == 200 and bool(ver)
                    return {"ok": ok, "detail": f"Grocy {ver.get('Version', '?')}" if ok else f"HTTP {r.status}"}
            if iid == "mealie":
                async with s.get(f"{url}/api/app/about", headers={"Authorization": f"Bearer {v.get('api_key', '')}"}) as r:
                    body = await r.json(content_type=None)
                    ok = r.status == 200 and isinstance(body, dict) and "version" in body
                    return {"ok": ok, "detail": f"Mealie {body.get('version', '?')}" if ok else f"HTTP {r.status}"}
            if iid == "overseerr":
                async with s.get(f"{url}/api/v1/status", headers={"X-Api-Key": v.get("api_key", "")}) as r:
                    body = await r.json(content_type=None)
                    ok = r.status == 200 and isinstance(body, dict) and "version" in body
                    return {"ok": ok, "detail": f"Overseerr {body.get('version', '?')}" if ok else f"HTTP {r.status}"}
            if iid == "unraid":
                hdr = {"x-api-key": v.get("api_key", ""), "Content-Type": "application/json"}
                async with s.post(url, headers=hdr, json={"query": "{ info { os { platform } } }"}) as r:
                    body = await r.json(content_type=None)
                    plat = ((((body or {}).get("data") or {}).get("info") or {}).get("os") or {}).get("platform")
                    return {"ok": bool(plat), "detail": f"Unraid ({plat})" if plat else f"HTTP {r.status}"}
            if iid == "searxng":
                async with s.get(url, params={"q": "connection test", "format": "json"}) as r:
                    ok = r.status == 200
                    return {"ok": ok, "detail": "search responding" if ok else f"HTTP {r.status}"}
            if iid == "embeddings":
                payload = {"model": v.get("model", ""), "input": "vera connection probe"}
                async with s.post(f"{url}/embeddings", json=payload) as r:
                    body = await r.json(content_type=None)
                    vec = (((body or {}).get("data") or [{}])[0] or {}).get("embedding")
                    ok = r.status == 200 and isinstance(vec, list) and len(vec) > 0
                    return {"ok": ok, "detail": f"embedding dim {len(vec)}" if ok else f"HTTP {r.status}"}
            if iid == "apple_reminders":
                async with s.get(f"{url}/health") as r:
                    body = await r.json(content_type=None)
                    granted = bool((body or {}).get("reminders_access"))
                    ok = r.status == 200 and granted
                    detail = ("bridge up, Reminders access granted" if ok else
                              "bridge up, Reminders access NOT granted" if r.status == 200
                              else f"HTTP {r.status}")
                    return {"ok": ok, "detail": detail}
            if iid == "reddit":
                async with s.post("https://www.reddit.com/api/v1/access_token",
                                  auth=aiohttp.BasicAuth(v.get("client_id", ""), v.get("client_secret", "")),
                                  data={"grant_type": "client_credentials"},
                                  headers={"User-Agent": v.get("user_agent") or "vera-scout/1.0"}) as r:
                    body = await r.json(content_type=None)
                    ok = r.status == 200 and bool((body or {}).get("access_token"))
                    return {"ok": ok, "detail": "app-only token acquired" if ok else f"HTTP {r.status}"}
    except Exception as e:  # noqa: BLE001 — a probe reports, never raises
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}
    return {"ok": False, "detail": "no probe defined"}


# ---- API --------------------------------------------------------------------------------

class FeatureUpdate(BaseModel):
    enabled: bool
    ack: bool = False


class IntegrationUpdate(BaseModel):
    fields: dict[str, str] | None = None
    enabled: bool | None = None
    features: dict[str, FeatureUpdate] | None = None


class TestBody(BaseModel):
    fields: dict[str, str] | None = None


@router.get("/integrations", tags=["integrations"])
async def list_integrations():
    doc = store.load()
    return {"integrations": [_entry(iid, doc) for iid in REGISTRY]}


@router.put("/integrations/{iid}", tags=["integrations"])
async def update_integration(iid: str, req: IntegrationUpdate):
    if iid not in REGISTRY:
        raise HTTPException(status_code=404, detail=f"unknown integration '{iid}'")
    spec = REGISTRY[iid]
    known = {f["id"]: f for f in spec["fields"]}

    if req.fields:
        for fid, val in req.fields.items():
            if fid not in known:
                raise HTTPException(status_code=422, detail=f"unknown field '{fid}'")
            if env_compat.is_set(known[fid]["env"]):
                raise HTTPException(
                    status_code=409,
                    detail=f"'{fid}' is pinned by env ({known[fid]['env']}). Change it there")
        store.update(iid, fields={k: v.strip() for k, v in req.fields.items()})

    if req.enabled is not None:
        if req.enabled and not _resolved(iid)["configured"]:
            missing = [f["env"] for f in spec["fields"]
                       if not f.get("optional") and not _resolved(iid)["values"][f["id"]]]
            raise HTTPException(status_code=400,
                                detail=f"cannot enable: missing {', '.join(missing)}")
        store.update(iid, enabled=req.enabled)

    if req.features:
        feature_ids = {ft["id"] for ft in spec.get("features", [])}
        for fid, fu in req.features.items():
            if fid not in feature_ids:
                raise HTTPException(status_code=422, detail=f"unknown feature '{fid}'")
            if fu.enabled:
                if not _resolved(iid)["enabled"]:
                    raise HTTPException(
                        status_code=409,
                        detail=f"enable {spec['display_name']} before its '{fid}' feature")
                already_acked = _feature_state(iid, fid)["acked"]
                if not already_acked and not fu.ack:
                    ram = next(ft["ramifications"] for ft in spec["features"] if ft["id"] == fid)
                    raise HTTPException(
                        status_code=400,
                        detail=f"'{fid}' is experimental and requires ack: true. {ram}")
                store.update_feature(iid, fid, enabled=True,
                                     acked_at=None if already_acked else time.time())
            else:
                store.update_feature(iid, fid, enabled=False)

    return _entry(iid, store.load())


@router.post("/integrations/{iid}/test", tags=["integrations"])
async def test_integration(iid: str, req: TestBody | None = None):
    if iid not in REGISTRY:
        raise HTTPException(status_code=404, detail=f"unknown integration '{iid}'")
    values = dict(_resolved(iid)["values"])
    if req and req.fields:
        values.update({k: v.strip() for k, v in req.fields.items() if k in
                       {f["id"] for f in REGISTRY[iid]["fields"]}})
    result = await _probe(iid, values)
    _last_test[iid] = {**result, "ts": time.time()}
    return result
