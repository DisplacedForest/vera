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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import env_compat
from . import integrations_store as store
from .integrations_probes import probe as _probe
from .integrations_registry import REGISTRY, _FEATURE_KILL_SWITCH

router = APIRouter()

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
