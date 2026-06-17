"""Profile Graph migration — a one-time, idempotent seed from the existing structured
stores (interests -> interest nodes, journal -> watch nodes, hand-mapped facts -> typed
nodes). Re-running must be a no-op: identical node counts and stable engagement, so the
seed is safe to run at every boot until the extraction job takes over. Run under pytest."""
import os

import pytest

from routers import profile_graph_store as pg
from routers import profile_graph_migrate as mig


@pytest.fixture(autouse=True)
def _fresh(tmp_path):
    pg.DB_PATH = os.path.join(str(tmp_path), "graph.db")
    pg.init()
    yield


INTERESTS = [
    {"topic": "Nottingham Forest", "gloss": "English football club", "weight": 1.0},
    {"topic": "winemaking", "gloss": "fermentation chemistry", "weight": 1.0},
]
JOURNAL = [
    {"heading": "Lumber Prices and Construction Costs",
     "resolve_condition": "sustained 10% move", "next_check": 1_700_000_000},
]
SEED_FACTS = [
    {"type": "location", "label": "Franklin",
     "facts": ["grew up here", "moved away ~20y ago", "suppress local Indiana news"]},
]


def test_migration_seeds_typed_nodes_with_facts():
    mig.migrate(interests=INTERESTS, journal_entries=JOURNAL, seed_facts=SEED_FACTS)
    by_label = {n["label"]: n for n in pg.all_nodes()}
    forest = by_label["Nottingham Forest"]
    assert forest["type"] == "interest"
    gloss = forest["facts"][0]
    assert gloss["text"] == "English football club"        # gloss became a provenance-bearing fact
    assert gloss["source"] == "migration:interest"
    assert by_label["Lumber Prices and Construction Costs"]["type"] == "watch"
    assert by_label["Lumber Prices and Construction Costs"]["state"] == "active"
    franklin = by_label["Franklin"]
    assert franklin["type"] == "location"
    assert len(franklin["facts"]) == 3
    assert all(f["source"] == "migration:seed" for f in franklin["facts"])


def test_migration_is_idempotent():
    mig.migrate(interests=INTERESTS, journal_entries=JOURNAL, seed_facts=SEED_FACTS)
    first = pg.all_nodes()
    eng_first = {n["label"]: n["engagement"] for n in first}
    mig.migrate(interests=INTERESTS, journal_entries=JOURNAL, seed_facts=SEED_FACTS)
    second = pg.all_nodes()
    assert len(second) == len(first)                       # no duplicate nodes on re-run
    eng_second = {n["label"]: n["engagement"] for n in second}
    assert eng_second == eng_first                         # engagement set, not re-accumulated
