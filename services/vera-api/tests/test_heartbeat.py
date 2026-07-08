import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routers import heartbeat as hb


def test_interests_text_lists_topics_with_stances():
    txt = hb._interests_text([{"topic": "mesh radios", "stance": "worth owning"},
                              {"topic": "root cellars"}])
    assert "- mesh radios — worth owning" in txt
    assert "- root cellars" in txt


def test_interests_text_empty_prompts_ranging():
    assert "none yet" in hb._interests_text([])


def test_decision_prompt_carries_every_section():
    usr = hb._decision_prompt("now", "the doc", "the interests", "the core",
                              "the home", "the devs", ["m1", "m2"], "the outcomes")
    for token in ("Now: now", "the doc", "the interests", "the core",
                  "the home", "the devs", "- m1\n- m2", "the outcomes"):
        assert token in usr


def test_decision_prompt_no_memories():
    assert "(none)" in hb._decision_prompt("n", "d", "i", "c", "h", "v", [], "r")


def test_learn_candidates_caps_at_two_by_default(monkeypatch):
    monkeypatch.delenv("HEARTBEAT_MAX_LEARN", raising=False)
    decision = {"learn": [{"topic": f"t{i}", "query": f"q{i}"} for i in range(5)]}
    assert hb._learn_candidates(decision) == [("t0", "q0"), ("t1", "q1")]


def test_learn_candidates_reads_env_bound(monkeypatch):
    monkeypatch.setenv("HEARTBEAT_MAX_LEARN", "4")
    decision = {"learn": [{"topic": f"t{i}", "query": f"q{i}"} for i in range(5)]}
    assert len(hb._learn_candidates(decision)) == 4


def test_learn_candidates_skips_incomplete_items(monkeypatch):
    monkeypatch.delenv("HEARTBEAT_MAX_LEARN", raising=False)
    decision = {"learn": [{"topic": "t0"}, {"topic": "t1", "query": "q1"}]}
    assert hb._learn_candidates(decision) == [("t1", "q1")]


def test_learn_candidates_empty_decision():
    assert hb._learn_candidates({}) == []


def test_refine_candidate_returns_new_document():
    assert hb._refine_candidate({"refine": {"content": "new plan"}}, "old plan") == "new plan"


def test_refine_candidate_rejects_same_or_empty():
    assert hb._refine_candidate({"refine": {"content": "same"}}, "same") is None
    assert hb._refine_candidate({"refine": {"content": "  "}}, "old") is None
    assert hb._refine_candidate({"refine": None}, "old") is None
    assert hb._refine_candidate({}, "old") is None


def test_proposal_target_prefers_entity_id():
    act = {"args": {"domain": "light", "data": {"entity_id": "light.porch"}}}
    assert hb._proposal_target(act) == "light.porch"


def test_proposal_target_falls_back_to_args_json():
    act = {"args": {"domain": "light"}}
    assert hb._proposal_target(act) == json.dumps({"domain": "light"})


def test_prompts_carry_no_fabrication_rules():
    from routers import tool_protocol
    assert hb.DECIDE_SYS.startswith(tool_protocol.LOOP_RULES)
    assert hb.GROUND_SYS.startswith(tool_protocol.LOOP_RULES)
