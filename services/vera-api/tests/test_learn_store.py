"""Learn store — card->node/feature links, outcome signals, the joined labeled set, and the
learned-weights row. Run under pytest."""
import os

import pytest

from routers import learn_store as ls


@pytest.fixture(autouse=True)
def _fresh(tmp_path):
    ls.DB_PATH = os.path.join(str(tmp_path), "learn.db")
    ls.init()
    yield


FEAT = {"relevance": 0.9, "novelty": 0.8, "opportunity": 0.2, "urgency": 0.1, "serendipity": 0.0}


def test_record_card_and_link_roundtrip():
    ls.record_card("c1", ["n1", "n2"], FEAT, now=100)
    link = ls.link("c1")
    assert link["nodes"] == ["n1", "n2"]
    assert link["features"]["relevance"] == 0.9
    assert ls.link("missing") is None


def test_record_card_is_idempotent_on_id():
    ls.record_card("c1", ["n1"], FEAT, now=100)
    ls.record_card("c1", ["n1", "n2"], FEAT, now=200)
    assert ls.link("c1")["nodes"] == ["n1", "n2"]


def test_outcomes_and_labeled_examples():
    ls.record_card("up", ["n1"], FEAT, now=1)
    ls.record_card("down", ["n2"], {**FEAT, "relevance": 0.1}, now=1)
    ls.record_card("orphan", ["n3"], FEAT, now=1)   # no outcome -> excluded from the labeled set
    ls.record_outcome("up", "up", now=2)
    ls.record_outcome("down", "down", now=2)
    examples = ls.labeled_examples()
    labels = {tuple(round(v, 3) for v in f.values()): y for f, y in examples}
    assert len(examples) == 2                         # only carded + outcomed rows
    assert any(y == 1 for _, y in examples) and any(y == 0 for _, y in examples)


def test_latest_outcome_wins_per_card():
    ls.record_card("c", ["n1"], FEAT, now=1)
    ls.record_outcome("c", "open", now=2)             # graded positive
    ls.record_outcome("c", "down", now=3)             # later thumb-down overrides
    examples = ls.labeled_examples()
    assert len(examples) == 1 and examples[0][1] == 0


def test_weights_get_set_roundtrip():
    assert ls.get_weights() is None
    coeffs = {"relevance": 0.4, "novelty": 0.3, "opportunity": 0.1, "urgency": 0.1, "serendipity": 0.1}
    ls.set_weights(coeffs, n_samples=250, now=500)
    w = ls.get_weights()
    assert w["coeffs"]["relevance"] == 0.4 and w["n_samples"] == 250
