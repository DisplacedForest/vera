import json
import os

from pydantic import BaseModel, ConfigDict, ValidationError


class _Out(BaseModel):
    model_config = ConfigDict(extra="allow")


class Decide(_Out):
    learn: list = []


class ForYouCandidate(_Out):
    surface: bool = False
    interest: str = ""
    topic: str = ""
    query: str = ""


class Relevance(_Out):
    related: bool = False
    link: str = ""


class Substance(_Out):
    briefing_worthy: bool = False


class Glosses(_Out):
    glosses: dict = {}


class Topics(_Out):
    topics: list = []


class NewsJudge(_Out):
    candidates: list = []


class Clusters(_Out):
    situations: list = []


def _extract(txt):
    try:
        return json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
    except Exception:
        return None


def _attempts():
    raw = os.environ.get("STRUCTURED_REPAIR_ATTEMPTS", "").strip()
    try:
        return max(0, int(raw)) if raw else 1
    except ValueError:
        return 1


def repairable(llm, msgs, **kw):
    return lambda fix: llm(msgs + ([{"role": "user", "content": fix}] if fix else []), **kw)


async def parsed(call, schema, *, repair=None):
    budget = _attempts() if repair is None else max(0, repair)
    errors = []
    fix = None
    for _ in range(budget + 1):
        raw = (await call(fix)) or ""
        data = _extract(raw)
        if data is None:
            reason = "reply did not contain a valid JSON object"
        else:
            try:
                return schema.model_validate(data).model_dump(), errors
            except ValidationError as e:
                first = e.errors()[0]
                loc = ".".join(str(p) for p in first.get("loc") or ()) or "payload"
                reason = f"{loc}: {first.get('msg', 'invalid')}"
        errors.append(reason)
        fix = (f"Your previous reply was not usable: {reason}.\n\n"
               f"Previous reply:\n{raw[:2000]}\n\n"
               "Reply again with ONLY a JSON object matching this schema, nothing else:\n"
               + json.dumps(schema.model_json_schema()))
    return None, errors
