"""Profile Graph store — typed node/edge graph with the three deterministic math
helpers (decay-on-read, cosine dedup-merge, spreading-activation). Run under pytest.

The math is unit-tested with hand-built vectors and graphs so nothing here touches a
live LLM or embeddings endpoint; the dedup tie-break and the embeddings call are
injected so the math path stays offline and deterministic."""
import asyncio
import os

import pytest

from routers import profile_graph_store as pg


@pytest.fixture(autouse=True)
def _fresh(tmp_path):
    pg.DB_PATH = os.path.join(str(tmp_path), "graph.db")
    pg.init()
    yield


def test_node_round_trips_aliases_and_facts():
    nid = pg.upsert_node(type="interest", label="winemaking",
                         aliases=["fermentation"], facts=["keeps a cellar"],
                         engagement=2.0)
    n = pg.get_node(nid)
    assert n["type"] == "interest"
    assert n["label"] == "winemaking"
    assert n["aliases"] == ["fermentation"]
    assert n["facts"] == ["keeps a cellar"]
    assert n["engagement"] == 2.0


def test_edge_links_nodes_and_neighbors_reads_back():
    a = pg.upsert_node(type="interest", label="hazelnuts")
    b = pg.upsert_node(type="project", label="food resilience")
    pg.add_edge(a, b, "supports", 0.8)
    nb = pg.neighbors(a)
    assert len(nb) == 1
    assert nb[0]["dst_id"] == b
    assert nb[0]["type"] == "supports"
    assert nb[0]["weight"] == 0.8


def test_engagement_decays_to_half_at_one_half_life():
    now = 1_000_000_000
    half_life_days = 23  # DECAY=0.97/day → ln(.5)/ln(.97) ≈ 22.76 days
    nid = pg.upsert_node(type="interest", label="forest",
                         engagement=1.0, last_engaged=now - half_life_days * 86400)
    n = pg.get_node(nid)
    assert pg.engagement_now(n, now=now) == pytest.approx(0.5, abs=0.02)


def test_decay_does_not_touch_facts():
    now = 1_000_000_000
    nid = pg.upsert_node(type="location", label="Franklin",
                         facts=["grew up here", "moved away ~20y ago"],
                         engagement=5.0, last_engaged=now - 365 * 86400)
    n = pg.get_node(nid)
    assert pg.engagement_now(n, now=now) < 0.001   # a year cold → effectively zero
    assert n["facts"] == ["grew up here", "moved away ~20y ago"]   # facts intact


def test_bump_decays_then_adds_and_restamps():
    now = 1_000_000_000
    nid = pg.upsert_node(type="interest", label="hazelnuts",
                         engagement=1.0, last_engaged=now - 23 * 86400)
    pg.bump_engagement(nid, now=now)   # decays 1.0→~0.5, then +INTERACTION_BONUS(1.0)
    n = pg.get_node(nid)
    assert n["engagement"] == pytest.approx(1.5, abs=0.02)
    assert n["last_engaged"] == now


# --- cosine dedup-merge: paraphrases of one topic collapse onto a single node ---

def _near(i):
    """A family of vectors all pairwise cos >= 0.90 (a small tilt off a shared axis)."""
    return [1.0, 0.05 * i, 0.02 * i]


def test_cosine_is_one_for_identical_and_zero_for_orthogonal():
    assert pg._cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert pg._cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_paraphrases_collapse_to_one_node():
    now = 1_000_000_000
    labels = [
        "Zone 7a passive cooling thermodynamics",
        "passive cooling in Zone 7a",
        "Zone 7a thermal mass cooling",
        "thermodynamics of Zone 7a passive cooling",
        "passive house cooling Zone 7a",
    ]
    ids = [pg.merge_or_create(type="interest", label=lbl, embedding=_near(i), now=now)
           for i, lbl in enumerate(labels)]
    assert len(set(ids)) == 1                       # one node, not five
    n = pg.get_node(ids[0])
    assert set(n["aliases"]) == set(labels)          # all five paraphrases recorded
    assert pg.engagement_now(n, now=now) == pytest.approx(5.0, abs=0.01)  # one accrued weight


def test_distant_label_makes_a_new_node():
    now = 1_000_000_000
    a = pg.merge_or_create(type="interest", label="winemaking",
                           embedding=[1.0, 0.0, 0.0], now=now)
    b = pg.merge_or_create(type="interest", label="UniFi networking",
                           embedding=[0.0, 1.0, 0.0], now=now)   # orthogonal → new node
    assert a != b


def test_gray_band_consults_the_tiebreak():
    now = 1_000_000_000
    a = pg.merge_or_create(type="interest", label="wine-os software",
                           embedding=[1.0, 0.0, 0.0], now=now)
    # cos ≈ 0.84 — inside [0.78, 0.90); the injected tiebreak says "different thing"
    b = pg.merge_or_create(type="interest", label="winemaking",
                           embedding=[1.0, 0.65, 0.0], now=now,
                           tiebreak=lambda label, node: False)
    assert a != b


# --- spreading activation: a finding inherits the engagement of connected nodes ---

def test_spreading_activation_sums_damped_neighbors_within_two_hops():
    now = 1_000_000_000
    a = pg.upsert_node(type="interest", label="hazelnuts", engagement=2.0, last_engaged=now)
    b = pg.upsert_node(type="project", label="food resilience", engagement=4.0, last_engaged=now)
    c = pg.upsert_node(type="interest", label="chicken forage", engagement=8.0, last_engaged=now)
    d = pg.upsert_node(type="interest", label="oil pressing", engagement=100.0, last_engaged=now)
    pg.add_edge(a, b, "supports", 0.8)
    pg.add_edge(b, c, "related_to", 0.5)
    pg.add_edge(c, d, "related_to", 1.0)   # d is 3 hops from a → excluded
    # a(2.0) + DAMPING·wAB·b + DAMPING²·wAB·wBC·c  = 2.0 + 0.5·0.8·4 + 0.25·0.8·0.5·8
    expected = 2.0 + 0.5 * 0.8 * 4.0 + 0.25 * 0.8 * 0.5 * 8.0
    assert pg.relevance([a], now=now) == pytest.approx(expected)
    assert pg.relevance([a], now=now) < 100.0   # the far oil-pressing node never leaks in


# --- embeddings helper: configured = a /v1/embeddings POST; unconfigured = clean None ---

def test_embed_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.delenv("VERA_EMBED_URL", raising=False)
    assert asyncio.run(pg.embed("anything")) is None   # degrades, never raises, no network


def test_embeddings_configured_flag(monkeypatch):
    monkeypatch.delenv("VERA_EMBED_URL", raising=False)
    assert pg.embeddings_configured() is False
    monkeypatch.setenv("VERA_EMBED_URL", "https://example.invalid/v1")
    assert pg.embeddings_configured() is True
