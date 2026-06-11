"""Pulse vein catalog — declarative manifests for the pinned ambient veins, plus the
opt-in state API. NOTHING is enabled by default: a fresh install shows an empty chip
row, and veins are activated through the app's Veins pane (or this API).

Pulse has two tiers: the research feed (image-rich briefings, core Vera, NOT a vein)
and a row of pinned ambient veins above it. Each vein groups one card `kind`; the Mac
app renders a slim chip per enabled vein that sits quiet until a producer posts a card
of that kind. Any card whose kind has no ENABLED vein falls back into the feed.

THE MANIFEST SCHEMA (the extensibility contract — a new vein is one entry here plus a
producer; the app hardcodes nothing and renders unknown veins/options generically):

  kind           card kind this vein groups (chip identity)
  label / icon / order / nominal_label    chip presentation (SF Symbol icon)
  blurb          one line: what this vein watches
  producer_jobs  scheduler job ids that feed the vein; all gate on the vein's enabled
                 state (first id is "primary" — its cron is the vein's editable schedule)
  requires       requirement dicts blocking enable while unmet:
                   {"kind": "integration", "id": <integrations registry id>}
                   {"kind": "feature", "integration": id, "feature": id}
                   {"kind": "env", "names": [...], "label": "human name"}
  providers      endpoint slots, declared as contracts (id, label, hint, default) —
                 point the vein at any compatible service, never a hardcoded pick
  options        groups of per-vein scoping fields the producer honors at run time:
                   {"group": label, "fields": [{id, label, type: bool|text|number|choice,
                    choices?, default, env?, hint?}]}
                 resolution per field: store value > env (when declared) > default —
                 the store is the runtime authority; env acts as the seed/headless layer.
"""

import os

import aiohttp
from fastapi import APIRouter
from pydantic import BaseModel

from . import vein_store

router = APIRouter()

MAX_ACTIVE = 6  # chip-row constraint, enforced at enable time

VEINS: list[dict] = [
    {
        "kind": "status", "label": "System", "icon": "gearshape", "order": 0,
        "nominal_label": "nominal",
        "blurb": "stack health and pending updates across your monitored sources",
        "producer_jobs": ["updates", "healthcheck"],
        "requires": [],
        "providers": [],
        "options": [
            {"group": "Monitored sources", "fields": [
                {"id": "src_containers", "label": "Containers", "type": "bool", "default": True,
                 "hint": "container-image updates from the host's update-status feed"},
                {"id": "src_host", "label": "Host OS", "type": "bool", "default": True},
                {"id": "src_home_assistant", "label": "Home Assistant + HACS", "type": "bool", "default": True},
                {"id": "src_network", "label": "Network gear", "type": "bool", "default": True},
                {"id": "src_apps", "label": "Apps", "type": "bool", "default": True},
                {"id": "src_service_health", "label": "Service health probes", "type": "bool", "default": True,
                 "hint": "liveness checks on the endpoints you've configured"},
            ]},
        ],
    },
    {
        "kind": "weather", "label": "Weather", "icon": "cloud.sun", "order": 1,
        "nominal_label": "clear",
        "blurb": "severe-weather pre-warnings for the home's coordinates",
        "producer_jobs": ["weather"],
        "requires": [{"kind": "env", "names": ["WEATHER_LAT", "WEATHER_LON"],
                      "label": "home coordinates"}],
        "providers": [
            {"id": "forecast_url", "label": "Forecast endpoint",
             "default": "https://api.open-meteo.com/v1/forecast",
             "hint": "any Open-Meteo-compatible forecast API; the default is the public instance"},
        ],
        "options": [
            {"group": "Units & thresholds", "fields": [
                {"id": "unit", "label": "Temperature unit", "type": "choice",
                 "choices": ["fahrenheit", "celsius"], "env": "TEMPERATURE_UNIT", "default": "fahrenheit"},
                {"id": "gust_threshold", "label": "Wind gust alert (mph)", "type": "number", "default": 45},
                {"id": "heat_threshold", "label": "Extreme heat alert", "type": "number", "default": None,
                 "hint": "in the configured unit; blank = 100F / 38C"},
                {"id": "freeze_threshold", "label": "Hard freeze alert", "type": "number", "default": None,
                 "hint": "in the configured unit; blank = 15F / -9C"},
            ]},
        ],
    },
    {
        "kind": "signals", "label": "Signals", "icon": "antenna.radiowaves.left.and.right",
        "order": 2, "nominal_label": "quiet",
        "blurb": "an external-watch monitor — only what crosses pre-declared thresholds",
        "producer_jobs": ["signals"],
        "requires": [],
        "providers": [],
        "options": [
            {"group": "Source groups", "fields": [
                {"id": "grp_financial", "label": "Financial stress", "type": "bool", "default": True,
                 "hint": "Treasury yield curve, VIX, high-yield credit spreads"},
                {"id": "grp_geophysical", "label": "Geophysical", "type": "bool", "default": True,
                 "hint": "USGS earthquakes, GDACS disaster alerts"},
                {"id": "grp_civic", "label": "Civic (US)", "type": "bool", "default": False,
                 "hint": "FEMA declarations for your state, Federal Register emergency documents"},
                {"id": "grp_grid", "label": "Grid stress", "type": "bool", "default": False,
                 "hint": "EIA demand-vs-forecast for your balancing authority (needs an EIA key)"},
                {"id": "grp_news", "label": "News judge", "type": "bool", "default": True,
                 "hint": "web-search sweep of your configured news beats, LLM-judged"},
            ]},
            {"group": "Orientation", "fields": [
                {"id": "orientation", "label": "What clears the bar", "type": "text",
                 "env": "SIGNALS_ORIENTATION", "default": "",
                 "hint": "completes \"would plausibly …\"; empty = a neutral household bar"},
                {"id": "impact_goods", "label": "Affected-goods line on supply cards", "type": "bool",
                 "env": "SIGNALS_IMPACT_GOODS", "default": False},
            ]},
        ],
    },
    {
        "kind": "media", "label": "Media", "icon": "film", "order": 3,
        "nominal_label": "quiet",
        "blurb": "a weekly worth-adding digest for your media library",
        "producer_jobs": ["media_curate"],
        "requires": [{"kind": "integration", "id": "overseerr"}],
        "providers": [],
        "options": [
            {"group": "Curation", "fields": [
                {"id": "taste", "label": "Taste profile", "type": "text",
                 "env": "MEDIA_CURATION_TASTE", "default": "",
                 "hint": "hard rules and preferences; empty = neutral (popularity is a pool, not a ranking)"},
                {"id": "cap", "label": "Picks per digest", "type": "number",
                 "env": "MEDIA_CURATION_CAP", "default": 8},
            ]},
        ],
    },
]

_BY_KIND = {l["kind"]: l for l in VEINS}


# --------------------------------------------------------------------- state resolution

def manifest(kind: str) -> dict | None:
    return _BY_KIND.get(kind)


def is_enabled(kind: str) -> bool:
    """Whether a vein is on. Veins are opt-in: absent from the store means off."""
    return bool((vein_store.load().get(kind) or {}).get("enabled"))


def enabled_kinds() -> set[str]:
    doc = vein_store.load()
    return {l["kind"] for l in VEINS if (doc.get(l["kind"]) or {}).get("enabled")}


def veins() -> list[dict]:
    """ENABLED veins only, chip fields, ordered left→right — the `GET /pulse/veins` view."""
    on = enabled_kinds()
    return sorted(({k: l[k] for k in ("kind", "label", "icon", "order", "nominal_label")}
                   for l in VEINS if l["kind"] in on), key=lambda l: l["order"])


def _coerce(field: dict, value):
    t = field.get("type", "text")
    try:
        if value is None or value == "":
            return None
        if t == "bool":
            return value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes", "on")
        if t == "number":
            return float(value)
    except (TypeError, ValueError):
        return None
    return str(value)


def _field_default(field: dict):
    """env (when the field declares one) > manifest default."""
    env_name = field.get("env")
    if env_name:
        v = os.environ.get(env_name, "").strip()
        if v:
            return _coerce(field, v)
    return field.get("default")


def option_values(kind: str) -> dict:
    """One vein's effective option values: store > env > manifest default."""
    spec = _BY_KIND.get(kind)
    if not spec:
        return {}
    stored = (vein_store.load().get(kind) or {}).get("options") or {}
    out = {}
    for grp in spec.get("options", []):
        for f in grp["fields"]:
            out[f["id"]] = _coerce(f, stored[f["id"]]) if f["id"] in stored else _field_default(f)
    return out


def has_stored_options(kind: str) -> bool:
    """Whether the deployment has made any explicit option choice for this vein (the
    signal that the store, not env/auto behavior, is the authority)."""
    return bool((vein_store.load().get(kind) or {}).get("options"))


def provider_values(kind: str) -> dict:
    """One vein's effective provider endpoints: store > slot default."""
    spec = _BY_KIND.get(kind)
    if not spec:
        return {}
    stored = (vein_store.load().get(kind) or {}).get("providers") or {}
    return {s["id"]: (str(stored.get(s["id"]) or "").strip() or s.get("default") or "")
            for s in spec.get("providers", [])}


def _requirement_state(req: dict) -> dict:
    """(met, human detail) for one requirement dict."""
    from . import integrations
    if req["kind"] == "integration":
        spec = integrations.REGISTRY.get(req["id"], {})
        met = integrations.integration(req["id"]) is not None
        return {"label": spec.get("display_name", req["id"]), "met": met,
                "integration": req["id"],
                "detail": "" if met else f"connect {spec.get('display_name', req['id'])} in Plugins"}
    if req["kind"] == "feature":
        met = integrations.feature_enabled(req["integration"], req["feature"])
        return {"label": f"{req['integration']} · {req['feature']}", "met": met,
                "integration": req["integration"],
                "detail": "" if met else f"enable the {req['feature']} feature in Plugins"}
    if req["kind"] == "env":
        met = all(os.environ.get(n, "").strip() for n in req["names"])
        return {"label": req.get("label", ", ".join(req["names"])), "met": met,
                "detail": "" if met else f"set {' and '.join(req['names'])}"}
    return {"label": str(req), "met": False, "detail": "unknown requirement"}


def requirements(kind: str) -> list[dict]:
    return [_requirement_state(r) for r in _BY_KIND.get(kind, {}).get("requires", [])]


def gate_reason(kind: str) -> str | None:
    """Scheduler-gate hook: why this vein's producer jobs may not run, or None."""
    if not is_enabled(kind):
        label = _BY_KIND.get(kind, {}).get("label", kind)
        return f"the {label} vein is off. Enable it in Veins."
    unmet = [r for r in requirements(kind) if not r["met"]]
    if unmet:
        return f"the {_BY_KIND[kind]['label']} vein needs {unmet[0]['label']} ({unmet[0]['detail']})"
    return None


# --------------------------------------------------------------------- one-time seeding

# Artifacts only a running deployment writes (briefing history, heartbeat ticks,
# memory stores). Their presence next to the vein store is what distinguishes an
# upgrade from a fresh install — env config alone is not evidence, since a fresh
# install may arrive fully parameterized.
_UPGRADE_MARKERS = ("pulse.db", "heartbeat.db", "knowledge.db", "actions.db",
                    "home_events.db", "media.db", "vera_memory")


def _prior_deployment() -> bool:
    from . import vein_store
    base = os.path.dirname(os.path.abspath(vein_store.PATH))
    return any(os.path.exists(os.path.join(base, name)) for name in _UPGRADE_MARKERS)


def seed_states() -> dict[str, dict]:
    """Upgrade seeding (called once by vein_store): on a data volume that already ran
    Vera, a vein whose producer is demonstrably configured seeds enabled, so the
    deployment keeps its chip row across the upgrade. A fresh install seeds nothing —
    veins are opt-in no matter how much config is present."""
    if not _prior_deployment():
        return {}
    from . import integrations
    seeds: dict[str, dict] = {}
    coords = all(os.environ.get(n, "").strip() for n in ("WEATHER_LAT", "WEATHER_LON"))
    if coords:
        seeds["weather"] = {"enabled": True}
    # a region-anchored deployment (state or coordinates) has been running signals
    if os.environ.get("HOME_STATE", "").strip() or coords:
        seeds["signals"] = {"enabled": True}
    if integrations.integration("home_assistant") or integrations.integration("unraid"):
        seeds["status"] = {"enabled": True}
    if integrations.integration("overseerr"):
        seeds["media"] = {"enabled": True}
    return seeds


# --------------------------------------------------------------------- API

class VeinUpdate(BaseModel):
    enabled: bool | None = None
    options: dict | None = None
    providers: dict | None = None
    cron: str | None = None  # schedule of the vein's primary producer job


def _job_views(job_ids: list[str]) -> list[dict]:
    from . import scheduler
    rows = {j["id"]: j for j in scheduler.jobs_view()}
    return [{"id": jid, "label": rows[jid]["label"], "cron": rows[jid]["cron"],
             "enabled": rows[jid]["enabled"], "gated": rows[jid].get("gated")}
            for jid in job_ids if jid in rows]


def _entry(spec: dict, doc: dict) -> dict:
    kind = spec["kind"]
    state = doc.get(kind) or {}
    reqs = requirements(kind)
    opts = option_values(kind)
    provs = provider_values(kind)
    return {
        "kind": kind, "label": spec["label"], "icon": spec["icon"], "order": spec["order"],
        "nominal_label": spec["nominal_label"], "blurb": spec["blurb"],
        "enabled": bool(state.get("enabled")),
        "requires": reqs, "can_enable": all(r["met"] for r in reqs),
        "providers": [{**{k: s.get(k, "") for k in ("id", "label", "hint", "default")},
                       "value": provs.get(s["id"], "")} for s in spec.get("providers", [])],
        "options": [{"group": g["group"],
                     "fields": [{**{k: f.get(k) for k in ("id", "label", "type", "choices", "hint")},
                                 "value": opts.get(f["id"]), "default": f.get("default")}
                                for f in g["fields"]]}
                    for g in spec.get("options", [])],
        "jobs": _job_views(spec["producer_jobs"]),
    }


@router.get("/pulse/veins/catalog", tags=["pulse"])
async def catalog():
    """Every vein manifest merged with its runtime state — the Veins pane's feed."""
    doc = vein_store.load()
    return {"veins": [_entry(l, doc) for l in sorted(VEINS, key=lambda x: x["order"])],
            "active": len(enabled_kinds()), "cap": MAX_ACTIVE}


@router.put("/pulse/veins/{kind}", tags=["pulse"])
async def update_vein(kind: str, req: VeinUpdate):
    from fastapi import HTTPException
    spec = _BY_KIND.get(kind)
    if not spec:
        raise HTTPException(status_code=404, detail=f"unknown vein '{kind}'")

    if req.options:
        known = {f["id"]: f for g in spec.get("options", []) for f in g["fields"]}
        bad = [k for k in req.options if k not in known]
        if bad:
            raise HTTPException(status_code=422, detail=f"unknown option(s): {', '.join(bad)}")
    if req.providers:
        slots = {s["id"] for s in spec.get("providers", [])}
        bad = [k for k in req.providers if k not in slots]
        if bad:
            raise HTTPException(status_code=422, detail=f"unknown provider slot(s): {', '.join(bad)}")

    if req.enabled:
        unmet = [r for r in requirements(kind) if not r["met"]]
        if unmet:
            raise HTTPException(status_code=409,
                                detail=f"cannot enable: needs {unmet[0]['label']} ({unmet[0]['detail']})")
        active = enabled_kinds()
        if kind not in active and len(active) >= MAX_ACTIVE:
            raise HTTPException(status_code=409,
                                detail=f"vein cap reached ({MAX_ACTIVE} active). Disable one first.")

    vein_store.update(kind, enabled=req.enabled, options=req.options, providers=req.providers)
    if req.cron is not None and spec["producer_jobs"]:
        from croniter import croniter
        if not croniter.is_valid(req.cron):
            raise HTTPException(status_code=422, detail=f"invalid cron expression '{req.cron}'")
        from . import scheduler_store
        scheduler_store.set_override(spec["producer_jobs"][0], cron=req.cron)
    return _entry(spec, vein_store.load())


@router.post("/pulse/veins/{kind}/test", tags=["pulse"])
async def test_vein(kind: str):
    """Exercise the vein's provider slots / sources; per-slot results, nothing persisted."""
    from fastapi import HTTPException
    spec = _BY_KIND.get(kind)
    if not spec:
        raise HTTPException(status_code=404, detail=f"unknown vein '{kind}'")
    results = []
    if kind == "weather":
        url = provider_values("weather").get("forecast_url", "")
        lat = os.environ.get("WEATHER_LAT", "").strip()
        lon = os.environ.get("WEATHER_LON", "").strip()
        if not (lat and lon):
            results.append({"slot": "forecast_url", "ok": False,
                            "detail": "set WEATHER_LAT and WEATHER_LON first"})
        else:
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(url, params={"latitude": lat, "longitude": lon,
                                                  "current": "temperature_2m"},
                                     timeout=aiohttp.ClientTimeout(total=10)) as r:
                        body = await r.json(content_type=None)
                ok = r.status == 200 and isinstance(body, dict) and "current" in body
                results.append({"slot": "forecast_url", "ok": ok,
                                "detail": "forecast responding" if ok else f"HTTP {r.status}"})
            except Exception as e:  # noqa: BLE001 — a probe reports, never raises
                results.append({"slot": "forecast_url", "ok": False, "detail": f"{type(e).__name__}: {e}"})
    elif kind == "media":
        from . import integrations
        v = integrations.integration("overseerr")
        if not v:
            results.append({"slot": "overseerr", "ok": False, "detail": "Overseerr is not connected"})
        else:
            results.append({"slot": "overseerr", **(await integrations._probe("overseerr", v))})
    elif kind == "status":
        from . import integrations
        ha = integrations.integration("home_assistant") is not None
        un = integrations.integration("unraid") is not None
        results.append({"slot": "sources", "ok": ha or un,
                        "detail": ", ".join(filter(None, ["Home Assistant" if ha else None,
                                                          "Unraid" if un else None])) or
                                  "no update sources connected — connect Home Assistant or Unraid in Plugins"})
    elif kind == "signals":
        opts = option_values("signals")
        on = [g.removeprefix("grp_") for g in
              ("grp_financial", "grp_geophysical", "grp_civic", "grp_grid", "grp_news") if opts.get(g)]
        results.append({"slot": "source groups", "ok": bool(on),
                        "detail": ("watching: " + ", ".join(on)) if on else "every source group is off"})
    return {"results": results}
