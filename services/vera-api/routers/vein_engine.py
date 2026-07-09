import hashlib
import importlib.util
import json
import logging
import os
import re
import time
from datetime import datetime

import aiohttp

from . import structured
from . import vein_engine_store as engine_store

log = logging.getLogger("vera.veins")

FLOOR_MINUTES = int(os.environ.get("VEIN_LLM_FLOOR_MINUTES", "30"))
BLOCKS_DIR = os.environ.get("VEIN_BLOCKS_DIR", "/data/blocks.d")
FETCH_CHARS = 2500
LLM_BLOCKS = ("llm_judge", "llm_compose", "situation_cluster")
ACTIVE_SWEEPABLE = ("new", "seen")
SEVERITY_RANK = {"notice": 1, "alert": 2, "critical": 3}


class BlockError(Exception):
    def __init__(self, block: str, detail: str):
        super().__init__(f"{block}: {detail}")
        self.block = block
        self.detail = detail


_PLACEHOLDER = re.compile(r"\{(options|providers)\.([a-z0-9_]+)\}")


def template(value, ctx: dict):
    if not isinstance(value, str):
        return value

    def _sub(m):
        pool = ctx.get(m.group(1)) or {}
        v = pool.get(m.group(2))
        if v is None or v == "":
            raise ValueError(f"{m.group(1)}.{m.group(2)} is not configured")
        return str(v)

    return _PLACEHOLDER.sub(_sub, value)


async def _vera(messages, **kw):
    from . import pulse
    return await pulse._vera(messages, **kw)


async def _get(url: str) -> tuple[int, str]:
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
            return r.status, await r.text()


async def _ha_get(url: str, token: str, entity_id: str) -> str:
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{url}/api/states/{entity_id}",
                         headers={"Authorization": f"Bearer {token}"},
                         timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status >= 400:
                raise BlockError("ha_state", f"HTTP {r.status} for {entity_id}")
            body = await r.json(content_type=None)
    return str((body or {}).get("state", ""))


def _walk(data, path: str):
    node = data
    for part in path.lstrip("$").strip(".").split("."):
        if isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                raise ValueError(f"path '{path}' not found")
        elif isinstance(node, dict) and part in node:
            node = node[part]
        else:
            raise ValueError(f"path '{path}' not found")
    return node


def _as_float(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


async def _run_web_search(items, params, ctx):
    from . import websearch
    query = template(params.get("query", ""), ctx)
    if not query:
        raise BlockError("web_search", "params.query is required")
    try:
        resp = await websearch.search(websearch.SearchRequest(
            query=query, max_results=int(params.get("max_results", 5))))
    except Exception as e:
        raise BlockError("web_search", f"search unavailable: {getattr(e, 'detail', e)}")
    return items + [{"key": r.url, "title": r.title, "url": r.url,
                     "content": r.content, "published": r.published}
                    for r in resp.results]


async def _run_http_fetch(items, params, ctx):
    url = template(params.get("url", ""), ctx)
    if not url:
        raise BlockError("http_fetch", "params.url is required")
    status, text = await _get(url)
    if status >= 400:
        raise BlockError("http_fetch", f"HTTP {status} from {url}")
    path = params.get("extract") or ""
    item = {"key": f"{url}#{path}" if path else url,
            "title": params.get("label") or url, "url": url}
    if path:
        try:
            data = json.loads(text)
        except ValueError:
            raise BlockError("http_fetch",
                             f"the response body is not JSON (starts: {text[:60]!r})")
        try:
            leaf = _walk(data, path)
        except ValueError as e:
            raise BlockError("http_fetch", str(e))
        v = _as_float(leaf)
        if v is not None:
            item["value"] = v
        else:
            item["content"] = str(leaf)
    else:
        item["content"] = " ".join(text.split())[:FETCH_CHARS]
    return items + [item]


async def _run_ha_state(items, params, ctx):
    from . import integrations
    entity_id = template(params.get("entity_id", ""), ctx)
    if not entity_id:
        raise BlockError("ha_state", "params.entity_id is required")
    cfg = integrations.integration("home_assistant") or {}
    url, token = cfg.get("url", ""), cfg.get("token", "")
    if not (url and token):
        raise BlockError("ha_state", "home_assistant integration is not connected")
    state = await _ha_get(url.rstrip("/"), token, entity_id)
    item = {"key": entity_id, "title": entity_id, "content": state}
    v = _as_float(state)
    if v is not None:
        item["value"] = v
    return items + [item]


async def _run_trip_band(items, params, ctx):
    hi, lo = params.get("hi"), params.get("lo")
    if hi is None and lo is None:
        raise BlockError("trip_band", "at least one of params.hi / params.lo is required")
    field = params.get("field", "value")
    severity = params.get("severity", "alert")
    out = []
    for it in items:
        v = _as_float(it.get(field))
        if v is None:
            continue
        side = None
        if hi is not None and v >= float(hi):
            side = "hi"
        elif lo is not None and v <= float(lo):
            side = "lo"
        if side:
            out.append({**it, "side": side, "severity": severity,
                        "key": f"{it.get('key', '')}:{side}"})
    return out


class _Verdicts(structured._Out):
    verdicts: list = []


JUDGE_SYS = (
    "You judge findings for an ambient household monitor. A finding clears the bar when it {bar}. "
    "Judge relevance only, from the given text; never add findings or numbers. "
    'Reply with ONLY a JSON object: {{"verdicts": [{{"index": <int>, "keep": <bool>, '
    '"reason": "<short>"}}]}} with one verdict per finding.'
)


async def _run_llm_judge(items, params, ctx):
    bar = template(params.get("bar", ""), ctx)
    if not bar:
        raise BlockError("llm_judge", "params.bar is required")
    if not items:
        return []
    listing = json.dumps([{"index": i, "title": it.get("title", ""),
                           "content": (it.get("content") or "")[:500],
                           "value": it.get("value")}
                          for i, it in enumerate(items)], indent=2)
    msgs = [{"role": "system", "content": JUDGE_SYS.format(bar=bar)},
            {"role": "user", "content": listing}]
    obj, errs = await structured.parsed(
        structured.repairable(_vera, msgs, temperature=0.2, think="off"), _Verdicts)
    if obj is None:
        raise BlockError("llm_judge", "; ".join(errs) or "unusable reply")
    keep = {v.get("index"): (v.get("reason") or "").strip()
            for v in (obj.get("verdicts") or []) if v.get("keep")}
    return [{**it, "judge_reason": keep[i]} if keep[i] else it
            for i, it in enumerate(items) if i in keep]


COMPOSE_SYS = (
    "Write one ambient Pulse card from the finding you are given. Format exactly:\n"
    "HEADLINE: <a few words naming the situation>\n"
    "SUMMARY: <one complete sentence>\n"
    "===\n"
    "<the card body in markdown: a short briefing grounded ONLY in the finding, "
    "in your voice, plain punctuation, no invented facts>\n"
    "When the finding carries a series of numbers worth showing as tiles, the body may "
    "include ONE fenced block:\n"
    "```vera:stats\n"
    '{"cards":[{"value":"<big number>","label":"<short label>","sub":"<small detail>"}]}\n'
    "```\n"
    "with 2 to 6 cards, every value drawn from the finding, never invented. A "
    "```vera:chart block is also available for series data when the style asks for it."
)


async def _run_llm_compose(items, params, ctx):
    from . import persona
    style = template(params.get("style", ""), ctx)
    sys = COMPOSE_SYS + (f"\nStyle for the body: {style}" if style else "")
    out = []
    for it in items:
        facts = json.dumps({k: it.get(k) for k in
                            ("title", "content", "value", "side", "url", "published")
                            if it.get(k) is not None}, indent=2)
        raw = (await _vera([{"role": "system", "content": persona.voiced(sys)},
                            {"role": "user", "content": facts}], temperature=0.4)).strip()
        head, sep, rest = raw.partition("===")
        source = head if sep else raw
        hm = re.search(r"(?im)^\s*HEADLINE:\s*(.+)$", source)
        sm = re.search(r"(?im)^\s*SUMMARY:\s*(.+)$", source)
        body = rest.strip() if sep else re.sub(r"(?im)^\s*(HEADLINE|SUMMARY):.*\n?", "", raw).strip()
        out.append({**it,
                    "headline": hm.group(1).strip().strip('"') if hm else (it.get("title") or ""),
                    "summary": sm.group(1).strip().strip('"') if sm else "",
                    "body": body})
    return out


CLUSTER_SYS = (
    "Group today's vetted findings into DISTINCT situations — one per genuinely separate event. "
    "Merge ONLY findings about the SAME underlying event. Keep separate events separate even in "
    "one region.\n"
    'Return ONLY JSON: {"situations":[{"headline":"3-6 word noun phrase, no trailing punctuation",'
    '"members":[<finding index>,...],"query":"a focused web search to deepen THIS situation"}]}'
)


def _clip(s, n=48):
    s = s.strip()
    return s if len(s) <= n else (s[:n].rsplit(" ", 1)[0] or s[:n])


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "situation"


def _numbered(sources):
    return "\n".join(f"[{s['n']}] {s['title']}: {(s.get('content') or '')[:500]}" for s in sources)


async def _run_situation_cluster(items, params, ctx):
    from . import websearch
    if not items:
        return []
    clusters = []
    try:
        listing = json.dumps([{"i": i, "severity": it.get("severity", ""),
                               "title": it.get("title", ""), "detail": it.get("content", "")}
                              for i, it in enumerate(items)], indent=2)
        msgs = [{"role": "system", "content": CLUSTER_SYS},
                {"role": "user", "content": f"Today's findings:\n{listing}"}]
        obj, errs = await structured.parsed(
            structured.repairable(_vera, msgs, temperature=0.2, think="off"), structured.Clusters)
        clusters = (obj or {}).get("situations") or []
        if obj is None and errs:
            log.warning("situation_cluster: %s", "; ".join(errs))
    except Exception as e:
        log.warning("situation_cluster: %s", e)
    if not clusters:
        clusters = [{"headline": _clip(it.get("title", "situation")), "members": [i],
                     "query": it.get("title", "")} for i, it in enumerate(items)]
    deepen_query = ""
    if params.get("deepen_query"):
        try:
            deepen_query = template(params["deepen_query"], ctx)
        except ValueError:
            deepen_query = ""
    out = []
    for cl in clusters:
        members = [items[i] for i in cl.get("members", [])
                   if isinstance(i, int) and 0 <= i < len(items)]
        if not members:
            continue
        severity = max((m.get("severity", "notice") for m in members),
                       key=lambda x: SEVERITY_RANK.get(x, 0))
        headline = (cl.get("headline") or members[0].get("title", "situation")).strip().rstrip(".,;:… ")
        sources, seen = [], set()
        for m in members:
            for s in m.get("trip_sources", []):
                if s.get("url") and s["url"] not in seen:
                    seen.add(s["url"])
                    sources.append({"n": len(sources) + 1, "title": s.get("title", "source"),
                                    "url": s["url"], "content": ""})
        flagged = [m for m in members if "deepen" in m]
        wants_deepen = any(m.get("deepen") for m in flagged) if flagged else True
        queries = [cl.get("query") or headline]
        if deepen_query and wants_deepen:
            queries.append(f"{headline} {deepen_query}")
        for q in queries:
            try:
                rs = await websearch.search(websearch.SearchRequest(
                    query=q, fetch_pages=2, max_results=4))
                for x in rs.results:
                    if x.url and x.url not in seen:
                        seen.add(x.url)
                        sources.append({"n": len(sources) + 1, "title": x.title or x.url,
                                        "url": x.url, "content": x.content or ""})
            except Exception as e:
                log.warning("situation_cluster research %s: %s", headline, e)
        facts = json.dumps([{"title": m.get("title"), "detail": m.get("content")}
                            for m in members], indent=2)
        content = (f"Situation: {headline}\n\nVetted findings:\n{facts}"
                   f"\n\nNumbered sources:\n{_numbered(sources)}")
        out.append({"key": f"sit:{_slug(headline)}", "title": headline,
                    "content": content, "severity": severity,
                    "sources": [{"n": s["n"], "title": s["title"], "url": s["url"]}
                                for s in sources]})
    return out


BLOCKS = {
    "web_search": _run_web_search,
    "http_fetch": _run_http_fetch,
    "ha_state": _run_ha_state,
    "trip_band": _run_trip_band,
    "llm_judge": _run_llm_judge,
    "llm_compose": _run_llm_compose,
    "situation_cluster": _run_situation_cluster,
}

_REQUIRED_PARAMS = {
    "web_search": "query",
    "http_fetch": "url",
    "ha_state": "entity_id",
    "llm_judge": "bar",
}


MONITOR_BLOCKS: set[str] = {"trip_band", "situation_cluster"}

BLOCK_NOTES: dict[str, str] = {}


def register(name: str, runner, monitor: bool = False, describe: str = "") -> None:
    BLOCKS[name] = runner
    if monitor:
        MONITOR_BLOCKS.add(name)
    if describe:
        BLOCK_NOTES[name] = describe


def load_block_modules() -> list[str]:
    try:
        names = sorted(os.listdir(BLOCKS_DIR))
    except OSError:
        return []
    loaded = []
    for name in names:
        if not name.endswith(".py"):
            continue
        path = os.path.join(BLOCKS_DIR, name)
        try:
            spec = importlib.util.spec_from_file_location(f"vera_block_{name[:-3]}", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            loaded.append(name)
        except Exception as e:
            log.warning("block module %s failed to load: %s", name, e)
    if loaded:
        log.info("loaded block modules from %s: %s", BLOCKS_DIR, ", ".join(loaded))
    return loaded


def validate_pipeline(defn: dict) -> list[str]:
    errors = []
    for i, step in enumerate(defn.get("pipeline") or []):
        name = step.get("block", "")
        if name not in BLOCKS:
            errors.append(f"step {i}: unknown block '{name}'")
            continue
        params = step.get("params") or {}
        need = _REQUIRED_PARAMS.get(name)
        if need and not params.get(need):
            errors.append(f"step {i}: {name} needs params.{need}")
        if name == "trip_band" and params.get("hi") is None and params.get("lo") is None:
            errors.append(f"step {i}: trip_band needs params.hi or params.lo")
    return errors


def is_monitor(pipeline) -> bool:
    return any(s.get("block") in MONITOR_BLOCKS for s in pipeline)


def has_llm(pipeline) -> bool:
    return any(s.get("block") in LLM_BLOCKS for s in pipeline)


def _ensure_keys(items):
    for it in items:
        if not it.get("key"):
            seed = (it.get("title") or "") + (it.get("url") or "")
            it["key"] = hashlib.sha1(seed.encode()).hexdigest()[:16]


def _drop_seen(kind: str, items):
    _ensure_keys(items)
    unseen = set(engine_store.filter_unseen(kind, [it["key"] for it in items]))
    return [it for it in items if it["key"] in unseen]


def _content_sig(it: dict) -> str:
    facts = {k: it.get(k) for k in ("title", "content", "value", "side")
             if it.get(k) is not None}
    return hashlib.sha1(json.dumps(facts, sort_keys=True).encode()).hexdigest()[:12]


def _card_fields(it: dict) -> dict:
    body = it.get("body") or it.get("content") or ""
    if not body and it.get("value") is not None:
        body = f"{it.get('title', '')}: {it['value']}"
    return {
        "title": it.get("headline") or it.get("title") or "Vein update",
        "summary": it.get("summary") or "",
        "body": body,
        "severity": it.get("severity") or "notice",
        "sources": (it.get("sources")
                    or ([{"n": 1, "title": it.get("title") or it["url"], "url": it["url"]}]
                        if it.get("url") else [])),
        "situation_key": it["key"],
        "category": it.get("category"),
        "items": it.get("items"),
        "change_set": _content_sig(it),
    }


def _watch_topics(body: str) -> list[str]:
    m = re.search(r"(?im)^\s*Watching:\s*(.+)$", body or "")
    if not m:
        return []
    return [t for t in (p.strip().strip(".").strip()
                        for p in re.split(r",|;|\band\b", m.group(1)))
            if t and len(t) <= 60][:4]


def _active_cards(kind: str) -> list[dict]:
    from . import pulse
    return [c for c in pulse.store.list_cards()
            if c.get("kind") == kind and c.get("status") in ACTIVE_SWEEPABLE]


def _split_standing(items, active):
    _ensure_keys(items)
    by_key = {c["situation_key"]: c for c in active if c.get("situation_key")}
    fresh, standing = [], []
    for it in items:
        card = by_key.get(it["key"])
        if card is not None and card.get("change_set") == _content_sig(it):
            standing.append(card)
        else:
            fresh.append(it)
    return fresh, standing


async def run_definition(defn: dict, dry_run: bool = False, manual: bool = False) -> dict:
    from . import pulse, pulse_veins
    kind = defn.get("kind", "")
    if not defn.get("pipeline"):
        return {"ok": False, "detail": f"vein '{kind}' has no pipeline"}
    pipeline = defn["pipeline"]
    monitor = bool(defn.get("standing")) or is_monitor(pipeline)
    if has_llm(pipeline) and not manual and not dry_run:
        last = engine_store.last_run(kind)
        if last is not None and time.time() - last < FLOOR_MINUTES * 60:
            ago = int((time.time() - last) // 60)
            return {"ok": True, "skipped": "schedule floor",
                    "detail": f"last run {ago}m ago; the floor for LLM pipelines is {FLOOR_MINUTES}m"}
    ctx = {"kind": kind,
           "options": pulse_veins.option_values_for(defn),
           "providers": pulse_veins.provider_values_for(defn)}
    items, seen_filtered, steps = [], False, []
    standing, standing_split, llm_used = [], False, False
    for step in pipeline:
        name = step.get("block", "")
        runner = BLOCKS.get(name)
        if runner is None:
            return {"ok": False, "block": name, "detail": f"unknown block '{name}'"}
        if not seen_filtered and not standing_split and name in LLM_BLOCKS:
            if monitor:
                items, standing = _split_standing(items, _active_cards(kind))
                standing_split = True
            else:
                items = _drop_seen(kind, items)
                seen_filtered = True
        if name in LLM_BLOCKS and items:
            llm_used = True
        try:
            items = await runner(items, step.get("params") or {}, ctx)
        except BlockError as e:
            return {"ok": False, "block": e.block, "detail": e.detail, "steps": steps}
        except Exception as e:
            return {"ok": False, "block": name, "detail": f"{type(e).__name__}: {e}", "steps": steps}
        steps.append({"block": name, "items": len(items)})
    _ensure_keys(items)
    if not monitor and not seen_filtered:
        items = _drop_seen(kind, items)
    if monitor and not standing_split:
        items, standing = _split_standing(items, _active_cards(kind))
    cards = [_card_fields(it) for it in items]
    extra = {"standing": len(standing)} if standing else {}
    if dry_run:
        return {"ok": True, "dry_run": True, "situations": len(cards) + len(standing),
                "cards": cards, **extra, "steps": steps}
    if llm_used:
        engine_store.mark_run(kind)
    active = _active_cards(kind)
    current = ({c["situation_key"] for c in cards}
               | {c["situation_key"] for c in standing})
    if monitor:
        for c in active:
            if c.get("situation_key") and c["situation_key"] not in current:
                pulse.store.delete_card(c["id"])
    for card in cards:
        for c in active:
            if c.get("situation_key") == card["situation_key"]:
                pulse.store.delete_card(c["id"])
        await pulse._inject(card["title"], card["body"], kind=kind,
                            severity=card["severity"], summary=card["summary"],
                            sources=card["sources"], situation_key=card["situation_key"],
                            category=card["category"], change_set=card["change_set"],
                            items=card["items"])
    if defn.get("journal"):
        for card in cards:
            label = card["title"].split("·", 1)[-1].strip() or card["title"]
            watch = _watch_topics(card["body"])
            try:
                from . import editor
                await editor.author_watch(label, facts=[card["body"]],
                                          resolve_condition=", ".join(watch) or None,
                                          origin="self")
            except Exception as e:
                log.warning("vein %s journal watch failed: %s", kind, e)
    if standing:
        today = datetime.now(pulse.TZ).date().isoformat()
        for c in standing:
            if (c.get("day") or "") < today:
                pulse.store.insert_card({**c, "status": "new", "day": today})
    if not monitor:
        engine_store.record_seen(kind, sorted(current))
    return {"ok": True, "situations": len(cards) + len(standing),
            "cards": len(cards), **extra}


async def run_vein(kind: str, dry_run: bool = False, manual: bool = False) -> dict:
    from . import pulse_veins
    defn = pulse_veins.manifest(kind)
    if not defn or not defn.get("pipeline"):
        return {"ok": False, "detail": f"vein '{kind}' has no pipeline"}
    return await run_definition(defn, dry_run=dry_run, manual=manual)


def _make_handler(kind: str):
    async def _run():
        return await run_vein(kind)
    return _run


def dynamic_jobs() -> dict:
    from . import pulse_veins
    out = {}
    for d in pulse_veins._defs().values():
        if d.get("pipeline"):
            out[f"vein_{d['kind']}"] = (f"{d['label']} vein run", d["schedule"], _make_handler(d["kind"]))
    return out
