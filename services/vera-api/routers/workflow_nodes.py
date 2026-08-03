import json

from . import vein_engine
from . import workflow_executor


NOTIFY_TITLE_LIMIT = 5


def _field_text(item, field):
    value = item.get(field)
    return "" if value is None else str(value)


def _drop_persisted(items):
    from . import pulse
    for item in items:
        card_id = item.get("id")
        if card_id and pulse.store.get_card(card_id):
            pulse.store.delete_card(card_id)


def _persist_summary(item, summary):
    from . import pulse
    card_id = item.get("id")
    if card_id:
        card = pulse.store.get_card(card_id)
        if card:
            pulse.store.insert_card({**card, "summary": summary})


async def _filter_run(node, items, ctx):
    config = node.get("config") or {}
    field = (config.get("field") or "title").strip()
    operator = config.get("operator") or "contains"
    value = str(config.get("value") or "")
    action = config.get("action") or "keep"

    def matches(item):
        if operator == "present":
            return item.get(field) not in (None, "")
        if operator == "missing":
            return item.get(field) in (None, "")
        text = _field_text(item, field)
        if operator == "equals":
            return text == value
        if operator == "not_equals":
            return text != value
        if operator == "not_contains":
            return value.lower() not in text.lower()
        return value.lower() in text.lower()

    kept, dropped = [], []
    for item in items:
        (kept if matches(item) != (action == "drop") else dropped).append(item)
    _drop_persisted(dropped)
    ctx.summaries[node["id"]] = {"kept": len(kept), "dropped": len(dropped)}
    return kept


def _facts(item):
    return {key: item.get(key) for key in ("title", "summary", "body", "content", "value")
            if item.get(key) is not None}


async def _llm_step_run(node, items, ctx):
    from . import pulse
    config = node.get("config") or {}
    prompt = (config.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("LLM step needs a prompt")
    mode = config.get("mode") or "per_card"
    output = config.get("output") or "annotate"
    fetched = ctx.data.get("fetched") or {}
    ctx.summaries[node["id"]] = {"mode": mode, "output": output}
    if not items:
        return []

    async def reply_for(content):
        if fetched:
            content = {**content, "context": fetched}
        messages = [{"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(content, indent=2)}]
        return (await pulse._vera(messages, temperature=0.4)).strip()

    if mode == "set":
        reply = await reply_for({"cards": [_facts(item) for item in items]})
        if output == "drop_on_empty":
            if reply:
                return list(items)
            _drop_persisted(items)
            return []
        if output == "replace_summary":
            for item in items:
                _persist_summary(item, reply)
            return [{**item, "summary": reply} for item in items]
        return [{**item, "annotation": reply} for item in items]
    out, dropped = [], []
    for item in items:
        reply = await reply_for({"card": _facts(item)})
        if output == "drop_on_empty":
            if reply:
                out.append(item)
            else:
                dropped.append(item)
        elif output == "replace_summary":
            _persist_summary(item, reply)
            out.append({**item, "summary": reply})
        else:
            out.append({**item, "annotation": reply})
    _drop_persisted(dropped)
    return out


async def _http_fetch_run(node, items, ctx):
    config = node.get("config") or {}
    url = (config.get("url") or "").strip()
    if not url:
        raise ValueError("HTTP fetch needs a URL")
    params = {"url": url}
    extract = (config.get("extract") or "").strip()
    if extract:
        params["extract"] = extract
    fetched = await vein_engine.BLOCKS["http_fetch"]([], params, dict(ctx.template))
    entry = fetched[-1]
    value = entry.get("value") if entry.get("value") is not None else entry.get("content")
    key = (config.get("context_key") or "").strip() or "fetched"
    ctx.data.setdefault("fetched", {})[key] = value
    ctx.summaries[node["id"]] = {"context_key": key}
    return list(items)


async def _notify_run(node, items, ctx):
    from . import pulse
    config = node.get("config") or {}
    workflow_id = ctx.definition.get("id") or "workflow"
    headline = (config.get("headline") or "").strip() or f"{workflow_id.title()} workflow update"
    user_id = str(ctx.data.get("user_id") or "")
    key = f"workflow-notify:{workflow_id}:{node['id']}" + (f":{user_id}" if user_id else "")
    titles = [t for t in (str(item.get("title") or "").strip() for item in items) if t]
    count = len(items)
    summary = f"{count} card{'' if count == 1 else 's'} reached this step"
    lines = "\n".join(f"- {t}" for t in titles[:NOTIFY_TITLE_LIMIT])
    body = f"{summary}." + (f"\n\n{lines}" if lines else "")
    for card in pulse.store.list_cards(include_expired=True):
        if card.get("situation_key") == key:
            pulse.store.delete_card(card["id"])
    await pulse._inject(headline, body, summary=summary, kind="notify", severity="notice",
                        provenance="workflow", situation_key=key, user_id=user_id or None)
    ctx.summaries[node["id"]] = {"cards": count}
    return list(items)


workflow_executor.register("flow.filter", run=_filter_run)
workflow_executor.register("flow.llm_step", run=_llm_step_run, barrier=True)
workflow_executor.register("flow.http_fetch", run=_http_fetch_run, barrier=True)
workflow_executor.register("flow.notify", run=_notify_run, barrier=True)
