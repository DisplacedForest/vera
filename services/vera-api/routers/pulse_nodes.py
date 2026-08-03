import time

from . import workflow_executor
from . import workflow_store


def _meta_for(topic, d):
    meta = topic.get("_wf_meta")
    if meta:
        return meta
    interest = (topic.get("interest") or "").strip()
    return {"item": {"round": d["triage_state"]["rnd"], "title": topic.get("title"),
                     "angle": topic.get("angle"), "interest": interest or None},
            "interest": interest,
            "record": d.get("record") or {"proposed": [], "injected": [], "skipped": []}}


async def _triage_pull(node, ctx):
    from . import pulse as p
    d = ctx.data
    out = d["out"]
    if "triage_state" not in d:
        d["triage_state"] = {"rnd": 0, "buffer": []}
        d["exclusions"] = [c["title"] for c in p._recent_for_user(d["user_id"])]
        await p._vision(pause=True)
    s = d["triage_state"]
    while True:
        if len(out["injected"]) >= d["target"]:
            return None
        if s["buffer"]:
            topic = s["buffer"].pop(0)
            interest = (topic.get("interest") or "").strip()
            topic["_wf_meta"] = {"item": {"round": s["rnd"], "title": topic.get("title"),
                                          "angle": topic.get("angle"), "interest": interest or None},
                                 "interest": interest, "record": d["record"]}
            return [topic]
        rnd = s["rnd"]
        if rnd >= p.PULSE_TRIAGE_ROUNDS:
            return None
        want = d["target"] - len(out["injected"])
        try:
            topics = await p._select_topics(rnd, who=d["who"], persona=d["persona"],
                                            all_interests=d["interests"], memories=d["memories"],
                                            exclusions=d["exclusions"], want=want,
                                            recent_texts=d["exclusions"])
        except Exception as e:
            out["errors"].append(f"triage round {rnd + 1}: {e}")
            return None
        s["rnd"] = rnd + 1
        if not topics:
            return None
        record = {"proposed": [t.get("title") for t in topics], "injected": [], "skipped": []}
        d["exclusions"].extend(t["title"] for t in topics if t.get("title"))
        out["topics"].extend(record["proposed"])
        out["rounds"].append(record)
        d["record"] = record
        s["buffer"] = list(topics)
        ctx.summaries[node["id"]] = {"rounds": len(out["rounds"]), "proposed": len(out["topics"])}


async def _gates_run(node, items, ctx):
    from . import pulse as p
    d = ctx.data
    out = d["out"]
    passed = []
    for topic in items:
        meta = _meta_for(topic, d)
        item, interest, record = meta["item"], meta["interest"], meta["record"]
        if interest and d["shipped"].get(interest.lower(), 0) >= p.PULSE_MAX_PER_INTEREST:
            d["gates"]["interest_cap"] += 1
            out["errors"].append(
                f"skipped (interest cap): {topic.get('title')} — '{interest}' already shipped this run")
            out["skipped"].append(topic.get("title"))
            record["skipped"].append(topic.get("title"))
            item.update({"status": "cap", "gate": "interest_cap", "reason": "interest cap",
                         "detail": interest})
            out["items"].append(item)
            continue
        passed.append(topic)
    ctx.summaries[node["id"]] = {"gates": d["gates"]}
    return passed


async def _synthesis_run(node, items, ctx):
    from . import pulse as p
    d = ctx.data
    out = d["out"]
    cards = []
    for topic in items:
        meta = _meta_for(topic, d)
        topic.pop("_wf_meta", None)
        item, interest, record = meta["item"], meta["interest"], meta["record"]
        before = len(out["errors"])
        oc = {}
        try:
            research_args = {"who": d["who"], "user_id": d["user_id"], "idx": d["attempt"],
                             "provenance": "scheduled", "errors": out["errors"],
                             "defer_audit": True, "outcome": oc}
            if d["style"] != "rotating":
                research_args["style_profile"] = d["style"]
            card = await p.research_topic(topic, **research_args)
            if card:
                d["pending_audit"].append((card, card.pop("_corpus", [])))
                if topic.get("seed_node_id"):
                    from . import learn_store
                    learn_store.record_card(card["id"], [topic["seed_node_id"]],
                                            topic.get("scores") or {}, int(time.time()))
                out["injected"].append(card["title"])
                record["injected"].append(card["title"])
                item.update({"title": card["title"], "status": "injected",
                             "card_id": card["id"],
                             "cover_generated": oc.get("cover_generated", False),
                             "audit": None})
                d["items_by_card"][card["id"]] = item
                if interest:
                    d["shipped"][interest.lower()] = d["shipped"].get(interest.lower(), 0) + 1
                    p._stamp_interest(interest)
                cards.append(card)
            else:
                d["gates"][p._gate_kind(out["errors"][before:])] += 1
                out["skipped"].append(topic.get("title"))
                record["skipped"].append(topic.get("title"))
                item.update({"status": "killed", "gate": oc.get("gate"),
                             "reason": oc.get("reason"), "detail": oc.get("detail")})
            out["items"].append(item)
        except Exception as e:
            out["errors"].append(f"{topic.get('title')}: {e}")
            item.update({"status": "error", "reason": str(e)})
            out["items"].append(item)
        d["attempt"] += 1
    ctx.summaries[node["id"]] = {"cards": len(out["injected"])}
    return cards


async def _claim_audit_run(node, items, ctx):
    from . import pulse as p
    d = ctx.data
    out = d["out"]
    ctx.summaries[node["id"]] = {"audited": len(d["pending_audit"])}
    try:
        await p._audit_phase(d["pending_audit"], out["errors"], d["items_by_card"])
    finally:
        d["vision_resumed"] = True
        await p._vision(pause=False)
    return items


async def _cover_art_run(node, items, ctx):
    d = ctx.data
    generated = sum(1 for card in items
                    if d["items_by_card"].get(card.get("id"), {}).get("cover_generated"))
    d["covers_generated"] = d.get("covers_generated", 0) + generated
    ctx.summaries[node["id"]] = {"generated": d["covers_generated"], "style": d["style"]}
    return items


async def _visual_review_run(node, items, ctx):
    from . import pulse as p
    from . import pulse_images
    d = ctx.data
    out = d["out"]
    threshold = (node.get("config") or {}).get("threshold", 0.8)
    d["review_failed"] = []
    reviewed = 0
    for item in out["items"]:
        if item.get("status") != "injected" or not item.get("card_id"):
            continue
        card = p.store.get_card(item["card_id"])
        if not card or not card.get("image_url"):
            continue
        review = await pulse_images.review_cover(card["image_url"], card["title"],
                                                 card["summary"], card["body"])
        item["visual_review"] = review or {"accept": True, "reason": "vision unavailable"}
        accepted = not review or (review["accept"] and review.get("score", 1) >= threshold)
        reviewed += 1
        if d["run_id"]:
            workflow_store.record_visual_run(d["run_id"], card["id"], card["image_url"],
                                             item["visual_review"], 0,
                                             "accepted" if accepted else "retrying")
        if not accepted:
            d["review_failed"].append(item)
    ctx.summaries[node["id"]] = {"reviewed": reviewed, "threshold": threshold}
    return items


async def _cover_retry_run(node, items, ctx):
    from . import pulse as p
    d = ctx.data
    out = d["out"]
    max_attempts = (node.get("config") or {}).get("max_attempts", 1)
    attempted = 0
    for item in d.get("review_failed") or []:
        if not max_attempts:
            break
        card = p.store.get_card(item["card_id"])
        if not card:
            continue
        await p._vision(pause=True)
        try:
            image_url, tint, generated = await p.make_cover(
                card["title"], card["summary"], card["body"], {"title": card["title"]},
                [], 1_000_000 + d["attempt"], out["errors"], d["style"])
        finally:
            await p._vision(pause=False)
        if generated:
            card["image_url"] = image_url
            card["tint"] = tint
            p.store.insert_card(card)
            item["visual_retry"] = {"attempted": True, "published": True}
        else:
            item["visual_retry"] = {"attempted": True, "published": False}
        attempted += 1
        if d["run_id"]:
            workflow_store.record_visual_run(d["run_id"], card["id"],
                                             image_url if generated else card["image_url"],
                                             item["visual_review"], 1,
                                             "retried" if generated else "retry_failed")
    ctx.summaries[node["id"]] = {"attempted": attempted}
    return items


async def _inject_run(node, items, ctx):
    from . import pulse as p
    d = ctx.data
    out = d["out"]
    p._finalize_run(out, d["gates"], d["target"])
    ctx.summaries[node["id"]] = {"cards": len(out["injected"])}
    return items


workflow_executor.register("pulse.triage", pull=_triage_pull)
workflow_executor.register("pulse.gates", run=_gates_run)
workflow_executor.register("pulse.synthesis", run=_synthesis_run)
workflow_executor.register("pulse.claim_audit", run=_claim_audit_run, barrier=True)
workflow_executor.register("pulse.cover_art", run=_cover_art_run)
workflow_executor.register("pulse.visual_review", run=_visual_review_run, barrier=True)
workflow_executor.register("pulse.cover_retry", run=_cover_retry_run, barrier=True)
workflow_executor.register("pulse.inject", run=_inject_run, barrier=True)
