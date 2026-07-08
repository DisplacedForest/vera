"""Actions router — the typed execute primitive.

Generalizes the knowledge store's propose/token/commit into a registry of allowlisted verbs, each with a pure
validator/preview (action_spec.py) and an async executor here. Two lanes:
  - gated (default): propose -> stage (token) -> commit (execute once, audited, idempotent).
    Cards carry an action+token; the Mac app Confirms.
  - free (/actions/auto): verbs explicitly enrolled with `autonomous: True` execute with no
    confirm, audited with auto=true; oversight is post-hoc via a status card.
"""
import asyncio
import os
import time
import uuid

import aiohttp
from fastapi import APIRouter
from pydantic import BaseModel

from . import action_spec as spec
from . import action_store as store
from . import groom_common as gcm
from . import kitchen
from . import knowledge_store as ks
from . import media_store as mstore
from . import overseerr
from . import pulse_store as pstore
from .pulse import _inject

router = APIRouter()


def _integration(iid: str) -> dict | None:
    """Executor-side registry lookup at call time, so runtime toggles apply."""
    from . import integrations
    return integrations.integration(iid)


# ---- executors (async; do the real work) -----------------------------------

async def _x_ha(args):
    d, s = args.get("domain"), args.get("service")
    if not spec.ha_allowed(d, s):  # defense in depth (validator already gated propose)
        return {"ok": False, "error": f"{d}.{s} not allowlisted"}
    ha = _integration("home_assistant")
    if not ha:
        return {"ok": False, "error": "home_assistant integration not enabled"}
    async with aiohttp.ClientSession() as sess:
        async with sess.post(
            f"{ha['url']}/api/services/{d}/{s}",
            headers={"Authorization": f"Bearer {ha['token']}"},
            json=args.get("data") or {},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            return {"ok": r.status < 300, "status": r.status, "service": f"{d}.{s}"}


async def _x_knowledge_set(args):
    p = ks.propose("set", type=args.get("type"), name=args.get("name"),
                   attrs=args.get("attrs") or {}, source="action", actor="vera")
    return ks.commit(p["token"])


async def _x_knowledge_delete(args):
    p = ks.propose("delete", entity_id=args.get("entity_id"), type=args.get("type"),
                   name=args.get("name"), source="action", actor="vera")
    return ks.commit(p["token"])


async def _x_knowledge_reverify(args):
    """Re-stamp a durable fact's last_verified to NOW. Stamps at commit time, not propose
    time, so the date reflects when the human actually confirmed. Merges — preserves all other attrs."""
    import time as _t
    eid = args.get("entity_id")
    cur = ks.get(eid)
    if not cur:
        return {"ok": False, "error": f"unknown entity {eid}"}
    p = ks.propose("set", entity_id=eid, type=cur["type"], name=cur["name"],
                   attrs={"last_verified": int(_t.time())}, source="action", actor="vera")
    return ks.commit(p["token"])


async def _x_knowledge_promote(args):
    """Codify a type's schema (the groom card's flagged-proposal Approve). Reversible via knowledge.uncodify
    on the restore path. All-or-nothing — ks.promote refuses if any entity is invalid."""
    return ks.promote(args.get("type"), args.get("schema") or {}, by="owner")


async def _x_grocy(args):
    grocy = _integration("grocy")
    if not grocy:
        return {"ok": False, "error": "grocy integration not enabled"}
    path = f"/api/stock/products/{args['product_id']}/{args['op']}"
    async with aiohttp.ClientSession() as sess:
        async with sess.post(
            f"{grocy['url']}{path}", headers={"GROCY-API-KEY": grocy['api_key']},
            json={"amount": args["amount"]}, timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            return {"ok": r.status < 300, "status": r.status}


async def _x_health(args):
    out = {"ok": True, "vera_api": "ok"}
    ha = _integration("home_assistant")
    if not ha:
        out["home_assistant"] = "integration not enabled"
        return out
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(f"{ha['url']}/api/", headers={"Authorization": f"Bearer {ha['token']}"},
                                timeout=aiohttp.ClientTimeout(total=10)) as r:
                out["home_assistant"] = "reachable" if r.status < 300 else f"http {r.status}"
    except Exception as e:
        out["home_assistant"] = f"unreachable: {e}"
    return out


async def _x_mealie_import(args):
    return await kitchen.import_recipe(args["url"])


def _norm_image(ref: str) -> str:
    """Image ref minus @digest and :tag, tag-insensitive (handles registry:port/path). For matching
    the card row's image against the Unraid container list."""
    ref = (ref or "").split("@", 1)[0]
    colon, slash = ref.rfind(":"), ref.rfind("/")
    return ref[:colon] if colon > slash else ref


async def _x_docker_update(args):
    """Update one container via the official Unraid GraphQL API: resolve the
    container id (no docker socket in vera-api), then `docker.updateContainer` (pull + recreate
    from template). High-risk: bounces the running container. Confirm-gated upstream."""
    unraid = _integration("unraid")
    if not unraid:
        return {"ok": False, "error": "unraid integration not enabled"}
    api_url = unraid["url"]
    want_name = (args.get("name") or "").lstrip("/")
    want_image = _norm_image(args.get("image") or "")
    hdr = {"x-api-key": unraid["api_key"], "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as sess:
        async with sess.post(api_url, headers=hdr, timeout=aiohttp.ClientTimeout(total=20),
                             json={"query": "{ docker { containers { id names image isUpdateAvailable } } }"}) as r:
            data = await r.json()
        conts = (((data or {}).get("data") or {}).get("docker") or {}).get("containers") or []
        match = None
        for c in conts:
            names = [n.lstrip("/") for n in (c.get("names") or [])]
            if (want_name and want_name in names) or (want_image and _norm_image(c.get("image")) == want_image):
                match = c
                break
        if not match:
            return {"ok": False, "error": f"container not found: {want_name or want_image}"}
        mut = {"query": "mutation($id: PrefixedID!){ docker { updateContainer(id:$id){ id names state isUpdateAvailable } } }",
               "variables": {"id": match["id"]}}
        async with sess.post(api_url, headers=hdr, timeout=aiohttp.ClientTimeout(total=300), json=mut) as r:
            res = await r.json()
    if res.get("errors"):
        return {"ok": False, "error": str(res["errors"])[:200]}
    updated = (((res.get("data") or {}).get("docker") or {}).get("updateContainer") or {})
    # Unraid's updater only handles GUI/template-managed containers; for compose/manually-created
    # ones the underlying script no-ops ("Configuration not found") yet the mutation still returns
    # success. A real update flips isUpdateAvailable false — if it's still true, nothing happened.
    if updated.get("isUpdateAvailable"):
        return {"ok": False, "container": want_name or want_image,
                "error": "Unraid could not update this container (not GUI/template-managed)"}
    return {"ok": True, "container": want_name or want_image, "state": updated.get("state")}


async def _x_overseerr(args):
    try:
        return await overseerr.submit_request(
            args["media_type"], args["media_id"], args.get("seasons"), bool(args.get("is4k", False))
        )
    except Exception as e:
        return {"ok": False, "error": str(getattr(e, "detail", e))}


async def _x_skill_upsert(args):
    """Apply a confirmed OWUI-skill write: snapshot the revision, then upsert the skill. The
    only sanctioned path by which the model's skill text reaches OWUI."""
    from . import authoring
    sid = (args.get("id") or "").strip() or authoring._slug(args.get("name") or "")
    content = args.get("content") or ""
    authoring.store.snapshot(f"skill:{sid}", content, note=args.get("name") or sid)
    await authoring._skill_upsert(sid, args.get("name"), args.get("description"), content)
    return {"ok": True, "id": sid}


EXECUTORS = {
    "owui.skill_upsert": _x_skill_upsert,
    "ha.service": _x_ha,
    "knowledge.set": _x_knowledge_set,
    "knowledge.delete": _x_knowledge_delete,
    "knowledge.reverify": _x_knowledge_reverify,
    "knowledge.promote": _x_knowledge_promote,
    "kitchen.grocy_adjust": _x_grocy,
    "kitchen.mealie_import": _x_mealie_import,
    "health.check": _x_health,
    "overseerr_request": _x_overseerr,
    "docker.update": _x_docker_update,
}


def _stage(verb, args, source, actor, chat_id, message_id):
    """Validate + preview + stage. Returns (ok_dict, error_dict)."""
    s = spec.SPEC.get(verb)
    if not s or verb not in EXECUTORS:
        return None, {"ok": False, "error": f"unknown verb {verb}"}
    err = s["validate"](args)
    if err:
        return None, {"ok": False, "error": err}
    preview = s["preview"](args)
    token = store.stage(verb, args, preview, s["risk"], s["reversible"], source, actor, chat_id, message_id)
    return {"token": token, "preview": preview, "verb": verb,
            "risk": s["risk"], "reversible": s["reversible"]}, None


# ---- endpoints -------------------------------------------------------------

class Propose(BaseModel):
    verb: str
    args: dict = {}
    source: str = "chat"
    actor: str = "vera"
    chat_id: str | None = None
    message_id: str | None = None


@router.post("/actions/propose", tags=["actions"])
async def propose(p: Propose):
    ok, err = _stage(p.verb, p.args, p.source, p.actor, p.chat_id, p.message_id)
    return err or {"ok": True, **ok}


class Commit(BaseModel):
    token: str


async def _run_token(token: str) -> dict:
    """Execute a staged action once (idempotent). Returns {applied, result?, error?}."""
    p = store.get(token)
    if not p:
        return {"applied": False, "error": "unknown or expired token"}
    if p["status"] == "applied":  # idempotent replay
        return {"applied": False, "result": p["result"]}
    if p["status"] == "dismissed":
        return {"applied": False, "error": "action was dismissed"}
    executor = EXECUTORS.get(p["verb"])
    if not executor:
        return {"applied": False, "error": f"unknown verb {p['verb']}"}
    result = await executor(p["args"])
    # Only a successful result burns the token. A failed apply stays pending so a retry
    # re-runs; otherwise the content-hash token (no TTL) would replay the cached failure forever.
    ok = bool((result or {}).get("ok", True))
    store.set_result(token, result, "applied" if ok else "pending")
    return {"applied": ok, "result": result}


@router.post("/actions/commit", tags=["actions"])
async def commit(c: Commit):
    r = await _run_token(c.token)
    if r.get("error"):
        return {"ok": False, "applied": False, "error": r["error"]}
    return {"ok": True, "applied": r["applied"], "result": r.get("result")}


class Dismiss(BaseModel):
    token: str


@router.post("/actions/dismiss", tags=["actions"])
async def dismiss(d: Dismiss):
    store.dismiss(d.token)
    return {"ok": True}


# ---- the free lane (trust-graduated autonomy) -------------------------------

# Dedup window for the free lane: an auto-imported URL is never re-imported within this span.
AUTO_DEDUP_SECS = 30 * 86400


def _norm_url(u: str) -> str:
    """Dedup key normalization: case-fold scheme+host, drop the fragment and trailing slash,
    so trivially restyled links to the same page collide."""
    u = (u or "").strip().split("#", 1)[0].rstrip("/")
    parts = u.split("://", 1)
    if len(parts) == 2:
        scheme, rest = parts
        host, _, path = rest.partition("/")
        u = f"{scheme.lower()}://{host.lower()}" + (f"/{path}" if path else "")
    return u


class Auto(BaseModel):
    verb: str
    args: dict = {}
    source: str = "heartbeat"
    actor: str = "vera"


@router.post("/actions/auto", tags=["actions"])
async def auto(a: Auto):
    """Execute a verb WITHOUT a confirm gate — allowed only for verbs explicitly enrolled
    via `autonomous: True` in the spec (that allowlist is the entire boundary). No token, no
    pending row: validate, dedup, run the same executor the gated path uses, audit with
    auto=true. Oversight is post-hoc (the caller surfaces a status card)."""
    if not spec.is_autonomous(a.verb):
        return {"ok": False, "rejected": True,
                "error": f"{a.verb} is not enrolled for autonomous execution"}
    s = spec.SPEC[a.verb]
    err = s["validate"](a.args)
    if err:
        return {"ok": False, "error": err}
    url = a.args.get("url")
    if url:
        key = _norm_url(url)
        for row in store.auto_recent(a.verb, time.time() - AUTO_DEDUP_SECS):
            if _norm_url((row.get("args") or {}).get("url", "")) == key:
                return {"ok": True, "skipped": "duplicate", "verb": a.verb,
                        "result": row.get("result")}
    result = await EXECUTORS[a.verb](a.args)
    ok = bool((result or {}).get("ok", True))
    store.log_auto(a.verb, a.args, result, status="applied" if ok else "failed",
                   source=a.source, actor=a.actor)
    return {"ok": ok, "verb": a.verb, "result": result}


@router.get("/actions/registry", tags=["actions"])
async def registry():
    """The verb catalog — the discovery surface clients and tools read instead of
    hardcoding verbs, arg shapes, or the HA allowlist (which is deployment config)."""
    return {"verbs": [{"verb": k, "risk": v["risk"], "reversible": v["reversible"],
                       "autonomous": bool(v.get("autonomous")),
                       "summary": v.get("summary", ""), "args": v.get("args", "{}")}
                      for k, v in spec.SPEC.items() if k in EXECUTORS],
            "ha_allowlist": {"services": sorted(spec.HA_ALLOWED_SERVICES),
                             "domains": sorted(spec.HA_ALLOWED_DOMAINS)}}


@router.get("/actions/log", tags=["actions"])
async def log(limit: int = 50):
    return {"log": store.recent_log(limit)}


class ProposeCard(BaseModel):
    verb: str
    args: dict = {}
    title: str
    body: str
    source: str = "chat"
    actor: str = "vera"
    chat_id: str | None = None
    message_id: str | None = None
    kind: str = "status"           # action cards default to the System vein
    severity: str | None = None    # "notice" | "alert" | "critical" (null = neutral)
    category: str | None = None    # System-vein sub-group ("vera" | "infra" | "health" | "update")


@router.post("/actions/propose_card", tags=["actions"])
async def propose_card(p: ProposeCard):
    """Stage an action AND inject a Pulse card carrying it — the producer path for action cards.
    Propose-for-review cards land in the System vein (kind=status) via this path."""
    ok, err = _stage(p.verb, p.args, p.source, p.actor, p.chat_id, p.message_id)
    if err:
        return err
    action = {"verb": p.verb, "args": p.args, "risk": ok["risk"],
              "reversible": ok["reversible"], "preview": ok["preview"], "token": ok["token"]}
    res = await _inject(p.title, p.body, action=action, kind=p.kind, severity=p.severity,
                        category=p.category)
    return {"ok": True, "card_id": res.get("id"), **ok}


# ---- multi-item digest card -------------------------------------------------

class DigestItem(BaseModel):
    verb: str
    args: dict = {}
    title: str
    subtitle: str = ""
    media_type: str | None = None
    tmdb_id: int | None = None
    poster: str | None = None   # poster thumbnail URL
    link: str | None = None     # IMDb (or TMDB) page


class ProposeDigest(BaseModel):
    title: str
    body: str = ""
    kind: str = "media"
    severity: str | None = None
    items: list[DigestItem]


async def _build_digest_items(items_in: list) -> list:
    """Stage each item's action (token) and shape it for the card. Un-stageable items are dropped
    rather than failing the whole digest."""
    out = []
    for it in items_in:
        ok, err = _stage(it.verb, it.args, "scheduled", "vera", None, None)
        if err:
            continue
        out.append({
            "item_id": str(uuid.uuid4()),
            "title": it.title, "subtitle": it.subtitle,
            "media_type": it.media_type, "tmdb_id": it.tmdb_id,
            "poster": it.poster, "link": it.link,
            "action": {"verb": it.verb, "args": it.args, "token": ok["token"],
                       "preview": ok["preview"], "risk": ok["risk"], "reversible": ok["reversible"]},
            "state": "pending",
        })
    return out


async def propose_digest_card(title, body, items_in, kind="media", severity=None):
    """Create one Pulse card carrying N independently approve/skip-able items. Returns card_id + items."""
    items = await _build_digest_items(items_in)
    res = await _inject(title, body, items=items, kind=kind, severity=severity)
    return {"ok": True, "card_id": res["id"], "items": items}


@router.post("/actions/propose_digest", tags=["actions"])
async def propose_digest(p: ProposeDigest):
    return await propose_digest_card(p.title, p.body, p.items, p.kind, p.severity)


async def _apply_item(item: dict, decision: str) -> dict:
    """Apply one decision to a digest item (mutates item['state'] in place). Both decisions persist
    to the media store so a skipped title never resurfaces and an approved one isn't re-proposed."""
    mt, tid, title = item.get("media_type"), item.get("tmdb_id"), item.get("title", "")
    verb = (item.get("action") or {}).get("verb")
    if decision == "skip":
        item["state"] = "skipped"
        if mt and tid is not None:
            mstore.record(mt, tid, title, "skipped")
        # rejecting a flagged groom proposal records a durable don't-redo for the next run
        if verb == "knowledge.promote":
            gcm.suppress("knowledge", "promote", (item.get("action") or {}).get("args", {}).get("type", ""),
                         reason="flagged proposal rejected")
        return {"ok": True, "state": "skipped"}
    if decision == "approve":
        token = (item.get("action") or {}).get("token")
        run = await _run_token(token) if token else {"error": "no token"}
        ran_ok = (not run.get("error")) and bool((run.get("result") or {}).get("ok", True))
        if ran_ok:
            item["state"] = "approved"
            if mt and tid is not None:
                mstore.record(mt, tid, title, "approved")
            return {"ok": True, "state": "approved", "result": run.get("result")}
        return {"ok": False, "state": "pending",
                "error": run.get("error") or (run.get("result") or {}).get("error", "failed")}
    return {"ok": False, "error": "decision must be approve|skip"}


def _repoll_updates(card: dict, res: dict) -> None:
    """After a successful apply on the stack-updates card, re-run the System vein so the card
    reflects reality without waiting for the next scheduled run. Fire-and-forget and best-effort
    (the run does live HA/Unraid I/O); the applying client also refreshes."""
    if (card or {}).get("category") != "update" or not res.get("ok"):
        return

    async def _run():
        try:
            from . import vein_engine
            await vein_engine.run_vein("status", manual=True)
        except Exception:
            pass
    asyncio.create_task(_run())


class CardItemDecision(BaseModel):
    card_id: str
    item_id: str
    decision: str  # "approve" | "skip"


@router.post("/actions/card/item", tags=["actions"])
async def card_item(d: CardItemDecision):
    card = pstore.get_card(d.card_id)
    if not card:
        return {"ok": False, "error": "unknown card"}
    items = card.get("items") or []
    item = next((i for i in items if i.get("item_id") == d.item_id), None)
    if not item:
        return {"ok": False, "error": "unknown item"}
    if item.get("state") != "pending":  # idempotent
        return {"ok": True, "state": item["state"], "applied": False}
    res = await _apply_item(item, d.decision)
    pstore.set_items(d.card_id, items)
    _repoll_updates(card, res)
    return res


class CardAllDecision(BaseModel):
    card_id: str
    decision: str  # "approve" | "skip"


@router.post("/actions/card/all", tags=["actions"])
async def card_all(d: CardAllDecision):
    card = pstore.get_card(d.card_id)
    if not card:
        return {"ok": False, "error": "unknown card"}
    items = card.get("items") or []
    applied = 0
    any_ok = False
    for item in items:
        if item.get("state") == "pending":
            r = await _apply_item(item, d.decision)
            any_ok = any_ok or bool(r.get("ok"))
            applied += 1
    pstore.set_items(d.card_id, items)
    _repoll_updates(card, {"ok": any_ok})
    return {"ok": True, "applied": applied, "items": items}
