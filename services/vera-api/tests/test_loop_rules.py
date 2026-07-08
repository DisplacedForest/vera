from routers import heartbeat, research, tool_protocol


def test_loop_budget_default_when_unset(monkeypatch):
    monkeypatch.delenv("RESEARCH_MAX_ITERATIONS", raising=False)
    assert tool_protocol.loop_budget("RESEARCH_MAX_ITERATIONS", 4) == 4


def test_loop_budget_reads_env(monkeypatch):
    monkeypatch.setenv("RESEARCH_MAX_ITERATIONS", "2")
    assert tool_protocol.loop_budget("RESEARCH_MAX_ITERATIONS", 4) == 2


def test_loop_budget_default_on_garbage(monkeypatch):
    monkeypatch.setenv("RESEARCH_MAX_ITERATIONS", "many")
    assert tool_protocol.loop_budget("RESEARCH_MAX_ITERATIONS", 4) == 4


def test_loop_budget_floors_at_zero(monkeypatch):
    monkeypatch.setenv("RESEARCH_MAX_ITERATIONS", "-3")
    assert tool_protocol.loop_budget("RESEARCH_MAX_ITERATIONS", 4) == 0


def test_loop_rules_carry_the_four_disciplines():
    r = tool_protocol.LOOP_RULES
    assert "one tool call per turn" in r
    assert "does not exist" in r
    assert "running summary" in r
    assert "never from guesses" in r


def test_research_prompts_carry_the_rules():
    assert research.PLAN_SYS.startswith(tool_protocol.LOOP_RULES)
    assert research.SYN_SYS.startswith(tool_protocol.LOOP_RULES)


def test_heartbeat_prompts_carry_the_rules():
    assert heartbeat.DECIDE_SYS.startswith(tool_protocol.LOOP_RULES)
    assert heartbeat.GROUND_SYS.startswith(tool_protocol.LOOP_RULES)


def test_research_iteration_cap_reads_env(monkeypatch):
    monkeypatch.delenv("RESEARCH_MAX_ITERATIONS", raising=False)
    assert research._iteration_cap(4) == 4
    monkeypatch.setenv("RESEARCH_MAX_ITERATIONS", "1")
    assert research._iteration_cap(4) == 1
