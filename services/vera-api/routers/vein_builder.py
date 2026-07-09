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
        draft, problems = _check_draft(obj["draft"])
        if draft is None:
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
                    obj, draft, problems = fixed_obj, fixed, []
                else:
                    problems = p2
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
