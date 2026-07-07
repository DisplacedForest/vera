"""Analyst Agent — the five-term EV ranking. Every term is a pure deterministic function;
the only model call (the opportunity classifier) and all embeddings are injected, so the
suite is offline and the ranking is reproducible. Run under pytest."""
import asyncio
import json
import os
from datetime import datetime, timezone

import pytest

from routers import analyst
from routers import profile_graph_store as pg


def _epoch(date_str):
    return int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


NOW = _epoch("2026-06-17")


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    pg.DB_PATH = os.path.join(str(tmp_path), "graph.db")
    pg.init()
    monkeypatch.setenv("ANALYST_LOG_PATH", os.path.join(str(tmp_path), "analyst_log.jsonl"))
    monkeypatch.setattr(analyst, "LOG_PATH", os.path.join(str(tmp_path), "analyst_log.jsonl"))
    yield


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------- urgency (date math)


def test_urgency_past_event_decays_to_zero():
    assert analyst.urgency("2024-01-01", None, NOW) < 0.05


def test_urgency_event_today_is_full():
    assert analyst.urgency("2026-06-17", None, NOW) == pytest.approx(1.0)


def test_urgency_near_deadline_high():
    # 4 days out: clamp(7 / 4) -> 1.0
    assert analyst.urgency(None, "2026-06-21", NOW) == pytest.approx(1.0)


def test_urgency_far_deadline_scaled():
    # 14 days out: 7 / 14 -> 0.5
    assert analyst.urgency(None, "2026-07-01", NOW) == pytest.approx(0.5, abs=0.01)


def test_urgency_undated_baseline():
    assert analyst.urgency(None, None, NOW) == pytest.approx(analyst.UNDATED_URGENCY)


def test_urgency_passed_deadline_falls_back_to_baseline():
    assert analyst.urgency(None, "2026-06-01", NOW) == pytest.approx(analyst.UNDATED_URGENCY)


# --------------------------------------------------------------- novelty


def test_novelty_full_when_no_corpus():
    assert analyst.novelty([1.0, 0.0], []) == pytest.approx(1.0)
    assert analyst.novelty(None, [[1.0, 0.0]]) == pytest.approx(1.0)


def test_novelty_near_duplicate_floors_out():
    finding = [1.0, 0.0, 0.0]
    corpus = [[0.99, 0.01, 0.0], [0.0, 1.0, 0.0]]
    nov = analyst.novelty(finding, corpus)
    assert nov < analyst.NOVELTY_FLOOR


def test_novelty_orthogonal_is_high():
    assert analyst.novelty([1.0, 0.0], [[0.0, 1.0]]) == pytest.approx(1.0)


# --------------------------------------------------------------- relevance (normalized)


def _seed_node(nid, engagement, type="interest"):
    pg.upsert_node(id=nid, type=type, label=nid, engagement=engagement, last_engaged=NOW)


def test_relevance_max_normalized_across_run():
    _seed_node("hot", 5.0)
    _seed_node("warm", 1.0)
    vals = analyst.relevance_scores(["hot", "warm", "hot"], NOW)
    assert vals[0] == pytest.approx(1.0)            # top candidate normalizes to 1.0
    assert vals[1] == pytest.approx(0.2, abs=0.01)  # 1.0 / 5.0
    assert vals[2] == pytest.approx(1.0)
    assert all(0.0 <= v <= 1.0 for v in vals)


def test_relevance_all_zero_is_zero():
    _seed_node("a", 0.0)
    vals = analyst.relevance_scores(["a", "a"], NOW)
    assert vals == [0.0, 0.0]


def test_relevance_includes_neighbor_engagement():
    _seed_node("seed", 1.0)
    _seed_node("ally", 4.0)
    pg.add_edge("seed", "ally", "related_to", weight=1.0)
    # seed's relevance picks up ally's engagement via spreading activation, beating a lone node
    _seed_node("lonely", 1.0)
    vals = analyst.relevance_scores(["seed", "lonely"], NOW)
    assert vals[0] > vals[1]


# --------------------------------------------------------------- opportunity + project_connection


def test_project_connection_direct_active_project():
    pg.upsert_node(id="p", type="project", label="p", state="active", last_engaged=NOW)
    assert analyst.project_connection("p") == 1.0


def test_project_connection_linked_within_hops():
    _seed_node("i", 1.0)
    pg.upsert_node(id="proj", type="project", label="proj", state="active", last_engaged=NOW)
    pg.add_edge("i", "proj", "supports")
    assert analyst.project_connection("i") == 1.0


def test_project_connection_resolved_project_not_counted():
    _seed_node("i2", 1.0)
    pg.upsert_node(id="done", type="project", label="done", state="resolved", last_engaged=NOW)
    pg.add_edge("i2", "done", "supports")
    assert analyst.project_connection("i2") == 0.0


def test_project_connection_none_when_unlinked():
    _seed_node("solo", 1.0)
    assert analyst.project_connection("solo") == 0.0


def test_opportunity_is_actionable_times_connection():
    assert analyst.opportunity(0.8, 1.0) == pytest.approx(0.8)
    assert analyst.opportunity(0.8, 0.0) == 0.0


# --------------------------------------------------------------- serendipity


def test_serendipity_adjacent_node_scores_damping():
    _seed_node("top", 5.0)
    _seed_node("nbr", 1.0)
    pg.add_edge("top", "nbr", "related_to")
    assert analyst.serendipity("nbr", ["top"]) == pytest.approx(pg.DAMPING)


def test_serendipity_two_hops_attenuates():
    _seed_node("t", 5.0)
    _seed_node("mid", 1.0)
    _seed_node("far", 1.0)
    pg.add_edge("t", "mid", "related_to")
    pg.add_edge("mid", "far", "related_to")
    assert analyst.serendipity("far", ["t"]) == pytest.approx(pg.DAMPING ** 2)


def test_serendipity_zero_for_top_k_member():
    _seed_node("x", 5.0)
    assert analyst.serendipity("x", ["x"]) == 0.0


def test_serendipity_zero_when_disconnected():
    _seed_node("island", 1.0)
    _seed_node("other", 5.0)
    assert analyst.serendipity("island", ["other"]) == 0.0


# --------------------------------------------------------------- rank() orchestration


def _emb(mapping):
    async def f(text):
        return mapping.get(text)
    return f


def _classify(mapping):
    async def f(text):
        return mapping.get(text, {"actionable": 0.0, "deadline": None, "action": None})
    return f


def _cand(finding, url, seed, published=None):
    return {"finding_text": finding, "title": finding, "url": url,
            "published_date": published, "source": "news", "seed_node_id": seed}


def test_content_similarity_clamps_and_degrades():
    assert analyst.content_similarity(None, [1.0]) == 1.0
    assert analyst.content_similarity([1.0], None) == 1.0
    assert analyst.content_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert analyst.content_similarity([1.0, 0.0], [-1.0, 0.0]) == 0.0


def test_rank_relevance_scaled_by_content_similarity():
    pg.upsert_node(id="n", type="interest", label="n", engagement=2.0,
                   last_engaged=NOW, embedding=[1.0, 0.0])
    embed = _emb({"near": [1.0, 0.0], "far": [0.0, 1.0]})
    cands = [_cand("near", "un", "n"), _cand("far", "uf", "n")]
    out = _run(analyst.rank(cands, now=NOW, recent_card_texts=[], embed=embed,
                            classify=_classify({}), max_per_interest=5))
    scores = {c["url"]: c["scores"]["relevance"] for c in out["considered"]}
    assert scores["un"] > scores["uf"]
    assert scores["un"] == pytest.approx(1.0)


def test_rank_relevance_falls_back_when_node_unembedded():
    _seed_node("n", 2.0)
    embed = _emb({"near": [1.0, 0.0], "far": [0.0, 1.0]})
    cands = [_cand("near", "un", "n"), _cand("far", "uf", "n")]
    out = _run(analyst.rank(cands, now=NOW, recent_card_texts=[], embed=embed,
                            classify=_classify({}), max_per_interest=5))
    scores = {c["url"]: c["scores"]["relevance"] for c in out["considered"]}
    assert scores["un"] == scores["uf"]


def test_rank_floors_near_duplicate_before_classify():
    _seed_node("n", 1.0)
    seen = []

    async def classify(text):
        seen.append(text)
        return {"actionable": 0.0, "deadline": None, "action": None}

    embed = _emb({"dup": [1.0, 0.0], "fresh": [0.0, 1.0], "yesterday": [1.0, 0.0]})
    cands = [_cand("dup", "u1", "n"), _cand("fresh", "u2", "n")]
    out = _run(analyst.rank(cands, now=NOW, recent_card_texts=["yesterday"],
                            embed=embed, classify=classify, max_per_interest=5))
    urls = [c["url"] for c in out["chosen"]]
    assert "u1" not in urls and "u2" in urls
    assert "dup" not in seen                      # floored before the classifier runs


def test_rank_breakdown_and_reproducible():
    _seed_node("ai", 5.0)
    pg.upsert_node(id="proj", type="project", label="proj", state="active", last_engaged=NOW)
    pg.add_edge("ai", "proj", "supports")
    embed = _emb({"grant": [1.0, 0.0], "plain": [0.0, 1.0]})
    classify = _classify({"grant": {"actionable": 0.9, "deadline": "2026-06-20", "action": "apply"}})
    cands = [_cand("grant", "g", "ai"), _cand("plain", "p", "ai")]
    kw = dict(now=NOW, recent_card_texts=[], embed=embed, classify=classify, max_per_interest=5)
    out = _run(analyst.rank(cands, **kw))
    g = next(c for c in out["chosen"] if c["url"] == "g")
    assert set(g["scores"]) == {"relevance", "novelty", "opportunity", "urgency", "serendipity", "total"}
    assert g["scores"]["opportunity"] > 0.5      # actionable AND wired to an active project
    assert g["scores"]["urgency"] > 0.9          # 3-day deadline
    out2 = _run(analyst.rank(cands, **kw))
    assert [c["url"] for c in out["chosen"]] == [c["url"] for c in out2["chosen"]]


def test_rank_spread_cap_limits_per_node():
    _seed_node("solo", 5.0)
    embed = _emb({f"f{i}": [1.0, 0.0, float(i)] for i in range(4)})
    cands = [_cand(f"f{i}", f"u{i}", "solo") for i in range(4)]
    out = _run(analyst.rank(cands, now=NOW, recent_card_texts=[], embed=embed,
                            classify=_classify({}), max_cards=8, max_per_interest=1))
    assert len(out["chosen"]) == 1               # one node, capped at one card


def test_rank_reserves_serendipity_slot_for_adjacent_node():
    _seed_node("ai", 5.0)
    _seed_node("garden", 0.6)
    pg.add_edge("ai", "garden", "related_to")
    embed = _emb({"a0": [1.0, 0.0], "a1": [0.0, 1.0], "g": [1.0, 1.0]})
    cands = [_cand("a0", "a0", "ai"), _cand("a1", "a1", "ai"), _cand("g", "g", "garden")]
    out = _run(analyst.rank(cands, now=NOW, recent_card_texts=[], embed=embed,
                            classify=_classify({}), max_cards=2, max_per_interest=2))
    urls = [c["url"] for c in out["chosen"]]
    assert "g" in urls                           # adjacent lower-engagement node reserved in
    g = next(c for c in out["chosen"] if c["url"] == "g")
    assert g["reserved"] is True


def test_rank_logs_every_term_for_every_candidate():
    _seed_node("n", 1.0)
    embed = _emb({"x": [1.0, 0.0], "y": [0.0, 1.0]})
    cands = [_cand("x", "x", "n"), _cand("y", "y", "n")]
    _run(analyst.rank(cands, now=NOW, recent_card_texts=[], embed=embed,
                      classify=_classify({}), max_per_interest=5))
    lines = [l for l in open(analyst.LOG_PATH).read().splitlines() if l.strip()]
    rec = json.loads(lines[-1])
    assert len(rec["candidates"]) == 2
    for c in rec["candidates"]:
        assert {"relevance", "novelty", "opportunity", "urgency", "serendipity",
                "total", "chosen"} <= set(c)


def test_rank_no_embeddings_degrades_without_floor():
    _seed_node("n", 1.0)

    async def no_embed(text):
        return None

    cands = [_cand("a", "a", "n"), _cand("b", "b", "n")]
    out = _run(analyst.rank(cands, now=NOW, recent_card_texts=["x"], embed=no_embed,
                            classify=_classify({}), max_per_interest=5))
    # with no embeddings novelty is 1.0 for all; nothing is floored
    assert len(out["chosen"]) == 2
