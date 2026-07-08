import asyncio

from routers import structured


def _seq(*replies):
    fixes = []
    pending = list(replies)

    async def call(fix):
        fixes.append(fix)
        return pending.pop(0)

    return call, fixes


def test_valid_first_pass():
    call, fixes = _seq('{"topics": ["a", "b"]}')
    obj, errors = asyncio.run(structured.parsed(call, structured.Topics))
    assert obj == {"topics": ["a", "b"]}
    assert errors == []
    assert fixes == [None]


def test_prose_around_object_is_tolerated():
    call, _ = _seq('Sure, here you go: {"topics": ["a"]} hope that helps')
    obj, errors = asyncio.run(structured.parsed(call, structured.Topics))
    assert obj == {"topics": ["a"]}
    assert errors == []


def test_repair_fixes_second_pass():
    call, fixes = _seq("no json here at all", '{"topics": ["a"]}')
    obj, errors = asyncio.run(structured.parsed(call, structured.Topics))
    assert obj == {"topics": ["a"]}
    assert len(errors) == 1
    assert fixes[0] is None
    assert "no json here at all" in fixes[1]
    assert '"topics"' in fixes[1]


def test_type_violation_triggers_repair():
    call, fixes = _seq('{"topics": "not a list"}', '{"topics": []}')
    obj, errors = asyncio.run(structured.parsed(call, structured.Topics))
    assert obj == {"topics": []}
    assert len(errors) == 1
    assert "topics" in fixes[1]


def test_gives_up_returns_none():
    call, fixes = _seq("junk", "more junk")
    obj, errors = asyncio.run(structured.parsed(call, structured.Topics))
    assert obj is None
    assert len(errors) == 2
    assert len(fixes) == 2


def test_extra_keys_survive():
    call, _ = _seq('{"topics": [], "note": "kept"}')
    obj, _errors = asyncio.run(structured.parsed(call, structured.Topics))
    assert obj["note"] == "kept"


def test_repair_budget_from_env(monkeypatch):
    monkeypatch.setenv("STRUCTURED_REPAIR_ATTEMPTS", "2")
    call, fixes = _seq("junk", "junk", "junk")
    obj, errors = asyncio.run(structured.parsed(call, structured.Topics))
    assert obj is None
    assert len(fixes) == 3


def test_repair_keyword_overrides_env(monkeypatch):
    monkeypatch.setenv("STRUCTURED_REPAIR_ATTEMPTS", "5")
    call, fixes = _seq("junk", "junk")
    obj, errors = asyncio.run(structured.parsed(call, structured.Topics, repair=1))
    assert obj is None
    assert len(fixes) == 2


def test_zero_repair_is_single_shot():
    call, fixes = _seq("junk")
    obj, errors = asyncio.run(structured.parsed(call, structured.Topics, repair=0))
    assert obj is None
    assert len(fixes) == 1


def test_surface_schemas_default_to_todays_fallbacks():
    call, _ = _seq("{}")
    obj, errors = asyncio.run(structured.parsed(call, structured.ForYouCandidate))
    assert obj["surface"] is False
    assert obj["topic"] == "" and obj["query"] == ""
    assert errors == []


def test_decide_accepts_the_documented_shape():
    call, _ = _seq('{"learn": [{"topic": "t", "query": "q"}], "refine": null, "action": null}')
    obj, errors = asyncio.run(structured.parsed(call, structured.Decide))
    assert obj["learn"] == [{"topic": "t", "query": "q"}]
    assert errors == []
