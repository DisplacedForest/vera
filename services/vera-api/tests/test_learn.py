"""Feedback write-back + the learned-weight fit. Engagement math runs against a temp Profile
Graph; the logistic fit runs on synthetic labeled sets. Run under pytest."""
import asyncio
import os

import pytest

from routers import learn
from routers import learn_store as ls
from routers import profile_graph_store as pg


NOW = 1_750_000_000


@pytest.fixture(autouse=True)
def _fresh(tmp_path):
    pg.DB_PATH = os.path.join(str(tmp_path), "graph.db")
    pg.init()
    ls.DB_PATH = os.path.join(str(tmp_path), "learn.db")
    ls.init()
    yield


FEAT = {"relevance": 0.9, "novelty": 0.8, "opportunity": 0.2, "urgency": 0.1, "serendipity": 0.0}


def _node(nid, engagement):
    pg.upsert_node(id=nid, type="interest", label=nid, engagement=engagement, last_engaged=NOW)


# --------------------------------------------------------------- write-back


def test_thumb_up_adds_bonus():
    _node("n1", 1.0)
    ls.record_card("c1", ["n1"], FEAT, now=NOW)
    learn.apply_signal("c1", "up", now=NOW)
    assert pg.get_node("n1")["engagement"] == pytest.approx(1.0 + learn.UP_BONUS)


def test_thumb_down_scales():
    _node("n1", 2.0)
    ls.record_card("c1", ["n1"], FEAT, now=NOW)
    learn.apply_signal("c1", "down", now=NOW)
    assert pg.get_node("n1")["engagement"] == pytest.approx(2.0 * learn.DOWN_DECAY)


def test_open_is_graded_positive():
    _node("n1", 1.0)
    ls.record_card("c1", ["n1"], FEAT, now=NOW)
    learn.apply_signal("c1", "open", now=NOW)
    assert pg.get_node("n1")["engagement"] == pytest.approx(1.0 + learn.OPEN_BONUS)


def test_expire_is_mild_negative():
    _node("n1", 2.0)
    ls.record_card("c1", ["n1"], FEAT, now=NOW)
    learn.apply_signal("c1", "expire", now=NOW)
    eng = pg.get_node("n1")["engagement"]
    assert eng < 2.0 and eng == pytest.approx(2.0 * learn.EXPIRE_PENALTY)


def test_apply_signal_records_outcome_for_the_fit():
    _node("n1", 1.0)
    ls.record_card("c1", ["n1"], FEAT, now=NOW)
    learn.apply_signal("c1", "up", now=NOW)
    examples = ls.labeled_examples()
    assert len(examples) == 1 and examples[0][1] == 1


def test_unknown_card_is_a_noop():
    assert learn.apply_signal("missing", "up", now=NOW) == {"nodes": 0}


def test_thumb_up_reinforces_edges():
    _node("n1", 1.0)
    _node("n2", 1.0)
    pg.add_edge("n1", "n2", "related_to", weight=1.0)
    ls.record_card("c1", ["n1"], FEAT, now=NOW)
    learn.apply_signal("c1", "up", now=NOW)
    assert pg.neighbors("n1")[0]["weight"] > 1.0


# --------------------------------------------------------------- discussed-later


def test_reinforce_node_strong_positive():
    _node("n1", 1.0)
    learn.reinforce_node("n1", now=NOW)
    assert pg.get_node("n1")["engagement"] == pytest.approx(1.0 + learn.DISCUSSED_BONUS)


def test_discussed_later_fires_from_extraction_for_served_node():
    from routers import conversation_extract as ce
    _node("hazelnuts", 1.0)
    ls.record_card("c1", ["hazelnuts"], FEAT, now=NOW)          # the topic has served a card
    before = pg.get_node("hazelnuts")["engagement"]
    extracted = {"nodes": [{"type": "interest", "label": "hazelnuts", "facts": [],
                            "engagement_signal": 1.0}], "edges": [], "threads": []}
    asyncio.run(ce.merge_conversation({"conv_id": "x", "ts": NOW}, extracted, now=NOW))
    assert pg.get_node("hazelnuts")["engagement"] >= before + learn.DISCUSSED_BONUS


def test_feedback_endpoint_writes_back():
    from routers import feedback
    _node("n1", 1.0)
    ls.record_card("c1", ["n1"], FEAT, now=NOW)
    res = asyncio.run(feedback.submit(feedback.Feedback(kind="pulse", sentiment="up", card_id="c1")))
    assert res["write_back"]["nodes"] == 1
    assert pg.get_node("n1")["engagement"] > 1.0


# --------------------------------------------------------------- learned-weight fit


def _label_set(n, hi_relevance_positive=True):
    """n synthetic cards where the label tracks relevance (other features held constant), so a
    correct fit makes relevance the dominant coefficient."""
    for i in range(n):
        hi = (i % 2 == 0)
        feat = {"relevance": 0.9 if hi else 0.1, "novelty": 0.5,
                "opportunity": 0.5, "urgency": 0.5, "serendipity": 0.5}
        ls.record_card(f"c{i}", ["n"], feat, now=NOW)
        ls.record_outcome(f"c{i}", "up" if hi else "down", now=NOW)


def test_fit_below_threshold_noops(monkeypatch):
    monkeypatch.setattr(learn, "FIT_MIN_SAMPLES", 50)
    _label_set(10)
    assert learn.fit_weights() is None
    assert ls.get_weights() is None


def test_fit_above_threshold_installs_normalized_coeffs(monkeypatch):
    monkeypatch.setattr(learn, "FIT_MIN_SAMPLES", 20)
    _label_set(40)
    coeffs = learn.fit_weights(now=NOW)
    assert coeffs is not None
    assert sum(coeffs.values()) == pytest.approx(1.0)
    assert coeffs["relevance"] == max(coeffs.values())     # learned: relevance drove the outcome
    assert ls.get_weights()["n_samples"] == 40


def test_analyst_weights_prefer_learned_when_present():
    from routers import analyst
    assert analyst._weights()[0] == pytest.approx(analyst.W_RELEVANCE)   # none installed -> env
    ls.set_weights({"relevance": 0.6, "novelty": 0.1, "opportunity": 0.1,
                    "urgency": 0.1, "serendipity": 0.1}, n_samples=99, now=NOW)
    assert analyst._weights()[0] == pytest.approx(0.6)
