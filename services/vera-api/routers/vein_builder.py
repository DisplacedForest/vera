import json
import os

from fastapi import APIRouter
from pydantic import BaseModel

from . import structured, vein_engine, vein_schema

router = APIRouter()

BUILDER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "BUILDER.md")

_FALLBACK = (
    "You help the household draft a new ambient vein definition. Every turn reply with "
    'ONLY one JSON object: {"reply": "<your words>", "draft": <definition or null>, '
    '"recommended": ["<block names>"], "done": <bool>}. Drafts must validate against '
    "this JSON Schema:\n\n<<SCHEMA>>\n\nBlocks: web_search, http_fetch, ha_state, "
    "trip_band, llm_judge, llm_compose. Math decides trips; the model only judges "
    "relevance and composes prose. When a request needs a source these blocks cannot "
    "reach, say so plainly and offer the closest real approximation."
)


def _skill_text() -> str:
    try:
        with open(BUILDER_PATH, encoding="utf-8") as f:
            text = f.read().strip()
            if text:
                return text
    except OSError:
        pass
    return _FALLBACK


_DOCUMENTED_BLOCKS = {"web_search", "http_fetch", "ha_state", "trip_band",
                      "llm_judge", "llm_compose", "situation_cluster"}


def _registered_palette() -> str:
    extras = [n for n in sorted(vein_engine.BLOCKS) if n not in _DOCUMENTED_BLOCKS]
    if not extras:
        return ""
    lines = [f"- {n}: {vein_engine.BLOCK_NOTES[n]}" if n in vein_engine.BLOCK_NOTES else f"- {n}"
             for n in extras]
    return ("\n\n## Registered blocks on this deployment\n\n"
            "These code-backed blocks are also valid pipeline steps here, exactly like the "
            "palette above (they take no params unless their note says otherwise; never "
            "remove one from a working pipeline in favor of a generic reconstruction):\n"
            + "\n".join(lines))


def builder_prompt() -> str:
    return (_skill_text().replace("<<SCHEMA>>", json.dumps(vein_schema.json_schema()))
            + _registered_palette())


async def _vera(messages, **kw):
    from . import pulse
    return await pulse._vera(messages, **kw)


def _configured() -> bool:
    from . import pulse
    return bool(pulse.VERA_BASE and pulse.MODEL)


_DISABLED = {"ok": False, "disabled": True,
             "detail": "no model is configured. Set VERA_BASE and VERA_MODEL."}


class BuilderTurn(structured._Out):
    reply: str = ""
    draft: dict | None = None
    recommended: list = []
    done: bool = False


class TurnRequest(BaseModel):
    messages: list[dict]


class DryRunRequest(BaseModel):
    definition: dict


def _check_draft(raw: dict) -> tuple[dict | None, list[str]]:
    try:
        d = vein_schema.validate_definition(raw)
    except ValueError as e:
        return None, [str(e)[:500]]
    errors = vein_engine.validate_pipeline(d)
    return (None, errors) if errors else (d, [])


async def _model_turn(msgs: list[dict]):
    return await structured.parsed(
        structured.repairable(_vera, msgs, temperature=0.4, think="on"), BuilderTurn)


async def _repaired_draft(msgs: list[dict], obj: dict):
    draft, problems = _check_draft(obj["draft"])
    if draft is not None:
        return obj, draft, []
    echo = {k: obj.get(k) for k in ("reply", "draft", "recommended", "done")}
    repair = msgs + [
        {"role": "assistant", "content": json.dumps(echo)},
        {"role": "user", "content":
            "The draft failed validation: " + "; ".join(problems) +
            ". Reply again with the same JSON object shape, draft corrected."}]
    fixed_obj, _ = await _model_turn(repair)
    if fixed_obj is not None and fixed_obj.get("draft"):
        fixed, p2 = _check_draft(fixed_obj["draft"])
        if fixed is not None:
            return fixed_obj, fixed, []
        return obj, None, p2
    return obj, None, problems


@router.get("/pulse/veins/builder", tags=["pulse"])
async def status():
    return {"configured": _configured()}


@router.post("/pulse/veins/builder/turn", tags=["pulse"])
async def turn(req: TurnRequest):
    if not _configured():
        return _DISABLED
    msgs = [{"role": "system", "content": builder_prompt()}] + req.messages
    obj, errs = await _model_turn(msgs)
    if obj is None:
        return {"reply": "", "draft": None, "valid": False, "problems": errs,
                "recommended": [], "done": False}
    draft, problems = None, []
    if obj.get("draft"):
        obj, draft, problems = await _repaired_draft(msgs, obj)
    return {"reply": obj.get("reply") or "", "draft": draft, "valid": draft is not None,
            "problems": problems, "recommended": obj.get("recommended") or [],
            "done": bool(obj.get("done"))}


@router.post("/pulse/veins/builder/dry_run", tags=["pulse"])
async def dry_run(req: DryRunRequest):
    if not _configured():
        return _DISABLED
    d, problems = _check_draft(req.definition)
    if d is None:
        return {"ok": False, "would_post": [], "steps": [], "errors": problems}
    out = await vein_engine.run_definition(d, dry_run=True, manual=True)
    if not out.get("ok"):
        where = f"{out['block']}: " if out.get("block") else ""
        return {"ok": False, "would_post": [], "steps": out.get("steps") or [],
                "errors": [where + (out.get("detail") or "run failed")]}
    would = [{"title": c["title"], "summary": c["summary"], "body": c["body"],
              "severity": c["severity"]} for c in out.get("cards") or []]
    return {"ok": True, "would_post": would, "steps": out.get("steps") or [], "errors": []}


_AUTHOR_ADDENDUM = (
    "\n\nThis is a single-shot authoring request, not a conversation. Produce one complete, "
    "valid draft in this reply. Never ask a follow-up question; pick sensible defaults and "
    "note them in `reply`. If the request cannot be served by the available blocks, set "
    "`draft` to null and say plainly in `reply` what is missing."
)

_DAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


def _clock(minute: str, hour: str) -> str:
    return f"{int(hour):d}:{int(minute):02d}"


def _cadence(cron: str) -> str:
    parts = (cron or "").split()
    if len(parts) != 5:
        return f"on the schedule '{cron}'"
    minute, hour, dom, month, dow = parts
    plain = dom == "*" and month == "*"
    if plain and dow == "*":
        if minute == "*" and hour == "*":
            return "every minute"
        if minute.startswith("*/") and minute[2:].isdigit() and hour == "*":
            return f"every {int(minute[2:])} minutes"
        if minute.isdigit() and hour == "*":
            return "every hour"
        if minute.isdigit() and hour.startswith("*/") and hour[2:].isdigit():
            return f"every {int(hour[2:])} hours"
        if minute.isdigit() and hour.isdigit():
            return f"every day at {_clock(minute, hour)}"
    if plain and minute.isdigit() and hour.isdigit() and dow.isdigit() and int(dow) <= 7:
        return f"every {_DAYS[int(dow) % 7]} at {_clock(minute, hour)}"
    return f"on the schedule '{cron}'"


def _summary(d: dict) -> str:
    steps = " then ".join(s["block"].replace("_", " ") for s in d.get("pipeline") or [])
    return (f"'{d['label']}' will run {steps} {_cadence(d.get('schedule') or '')} "
            f"and post a card to your Pulse when something is worth saying.")


class AuthorRequest(BaseModel):
    mode: str
    request: str = ""
    draft: dict | None = None


async def _author_draft(request: str):
    from fastapi import HTTPException
    if not _configured():
        return _DISABLED
    text = (request or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="draft mode needs a plain-language request")
    msgs = [{"role": "system", "content": builder_prompt() + _AUTHOR_ADDENDUM},
            {"role": "user", "content": text}]
    obj, errs = await _model_turn(msgs)
    if obj is None:
        return {"ok": False, "draft": None, "summary": "", "reply": "", "problems": errs}
    draft, problems = None, []
    if obj.get("draft"):
        obj, draft, problems = await _repaired_draft(msgs, obj)
    if draft is None:
        return {"ok": False, "draft": None, "summary": "", "reply": obj.get("reply") or "",
                "problems": problems}
    return {"ok": True, "draft": draft, "summary": _summary(draft),
            "reply": obj.get("reply") or "", "problems": []}


async def _author_save(raw: dict | None):
    from fastapi import HTTPException
    from . import pulse_veins, vein_defs, vein_store
    if not raw:
        raise HTTPException(status_code=422,
                            detail="save mode needs the draft returned by draft mode")
    if raw.get("kind") in pulse_veins._defs():
        raise HTTPException(status_code=409,
                            detail=f"vein '{raw.get('kind')}' already exists")
    draft, problems = _check_draft(raw)
    if draft is None:
        raise HTTPException(status_code=422, detail="; ".join(problems))
    try:
        saved = vein_defs.save_custom(draft)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    kind = saved["kind"]
    unmet = [r for r in pulse_veins.requirements(kind) if not r["met"]]
    active = pulse_veins.enabled_kinds()
    if unmet:
        vein_store.update(kind, enabled=False)
        return {"ok": True, "kind": kind, "enabled": False, "summary": _summary(saved),
                "detail": f"saved but not enabled: needs {unmet[0]['label']} "
                          f"({unmet[0]['detail']})"}
    if len(active) >= pulse_veins.MAX_ACTIVE:
        vein_store.update(kind, enabled=False)
        return {"ok": True, "kind": kind, "enabled": False, "summary": _summary(saved),
                "detail": f"saved but not enabled: vein cap reached "
                          f"({pulse_veins.MAX_ACTIVE} active). Disable one first."}
    vein_store.update(kind, enabled=True)
    return {"ok": True, "kind": kind, "enabled": True, "summary": _summary(saved)}


@router.post("/pulse/veins/author", tags=["pulse"])
async def author(req: AuthorRequest):
    from fastapi import HTTPException
    if req.mode == "draft":
        return await _author_draft(req.request)
    if req.mode == "save":
        return await _author_save(req.draft)
    raise HTTPException(status_code=422, detail="mode must be 'draft' or 'save'")
