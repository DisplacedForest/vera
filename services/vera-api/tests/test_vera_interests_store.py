"""Vera's emergent interest store — salience accrual, fixation cooldown, fact-cluster
derivation. Run under pytest."""
import os
import time

import pytest

from routers import vera_interests_store as vi


@pytest.fixture(autouse=True)
def _fresh(tmp_path):
    vi.DB_PATH = os.path.join(str(tmp_path), "interests.db")
    vi.init()
    yield


def test_observe_accrues_salience():
    vi.observe("agent design", source="self")
    vi.observe("agent design", source="self")  # returning bumps it
    rows = {r["topic"]: r for r in vi.all_interests()}
    assert rows["agent design"]["salience"] == 2.0


def test_touch_cooldown_hides_from_active():
    vi.observe("topic A", salience_bump=5.0)
    vi.observe("topic B", salience_bump=1.0)
    assert vi.active()[0]["topic"] == "topic A"           # higher salience ranks first
    vi.touch("topic A")                                   # explored -> fixation cooldown
    active = [i["topic"] for i in vi.active()]
    assert "topic A" not in active and "topic B" in active  # cooled-down topic is hidden
    # ...but it still exists in the full list, now with a cooldown + explore count
    a = {r["topic"]: r for r in vi.all_interests()}["topic A"]
    assert a["times_explored"] == 1 and a["cooldown_until"] > int(time.time())


def test_cooled_names_the_cooling_subset():
    vi.observe("topic A")
    vi.observe("topic B")
    vi.touch("topic A")
    assert vi.cooled(["topic A", "topic B", "never seen"]) == {"topic A"}


def test_cooled_expires_with_the_clock():
    vi.observe("topic A")
    vi.touch("topic A")
    future = int(time.time()) + 10 * 24 * 3600
    assert vi.cooled(["topic A"], now=future) == set()
    assert vi.cooled([]) == set()


def test_active_tempers_salience_by_novelty():
    vi.observe("worked", salience_bump=4.0)
    vi.touch("worked"); vi.touch("worked"); vi.touch("worked")  # heavily explored
    vi.observe("fresh", salience_bump=2.0)                       # lower salience, never explored
    # cooldown would hide 'worked'; check ordering on a far-future clock where both are eligible
    future = int(time.time()) + 10 * 24 * 3600
    order = [i["topic"] for i in vi.active(now=future)]
    assert order.index("fresh") < order.index("worked")  # novelty lifts the fresh one above


def test_derive_from_facts_clusters_related_only():
    facts = [
        {"topic": "UniFi 8.5", "content": "unifi rf optimization for iot"},
        {"topic": "UniFi 8.6", "content": "unifi rf optimization for iot, improved"},
        {"topic": "Ashvale", "content": "ashvale rovers finished 16th"},
    ]
    observed = vi.derive_from_facts(facts)
    topics = {r["topic"]: r for r in vi.all_interests()}
    assert len(observed) == 1                       # only the UniFi pair clusters
    rep = topics[observed[0]]
    assert rep["source"] == "fact-cluster" and rep["salience"] >= 2.0
    assert "Ashvale" not in topics                  # a lone fact is not yet an interest
