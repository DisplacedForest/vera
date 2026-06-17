"""Editor Agent — date-aware staleness check, cross-domain links, journal-as-view, and the
Analyst-driven topic mapping. Graph-reading helpers run against a temp Profile Graph; all LLM
I/O is injected. Run under pytest."""
import asyncio
import os

import pytest

from routers import editor
from routers import profile_graph_store as pg


NOW = 1_750_000_000  # 2025-06-15; graph-decay tests pass this same value as `now`


@pytest.fixture(autouse=True)
def _fresh(tmp_path):
    pg.DB_PATH = os.path.join(str(tmp_path), "graph.db")
    pg.init()
    yield


# --------------------------------------------------------------- date-aware staleness


def test_stale_current_claim_flagged_despite_supporting_source():
    body = ("Sean Dyche is currently the Forest manager [1]. "
            "The City Ground opened in 1898 [2].")
    sources = [{"n": 1, "published": "2024-03-01"}, {"n": 2, "published": "2026-06-01"}]
    flagged = editor.stale_current_claims(body, sources, "2026-06-17", max_age_days=120)
    assert any("Dyche" in s for s in flagged)
    assert not any("City Ground" in s for s in flagged)   # past-tense, no present marker


def test_fresh_current_claim_not_flagged():
    body = "Forest is currently mid-table [1]."
    sources = [{"n": 1, "published": "2026-06-10"}]
    assert editor.stale_current_claims(body, sources, "2026-06-17", max_age_days=120) == []


def test_present_claim_without_citation_not_flagged():
    body = "It is currently raining outside."
    assert editor.stale_current_claims(body, [], "2026-06-17") == []


def test_mixed_citations_one_fresh_keeps_claim():
    # a present claim is stale only when EVERY cited source is old
    body = "The chip remains the fastest available [1][2]."
    sources = [{"n": 1, "published": "2023-01-01"}, {"n": 2, "published": "2026-06-15"}]
    assert editor.stale_current_claims(body, sources, "2026-06-17", max_age_days=120) == []


def test_audit_hedges_stale_claim_even_when_auditor_clean(monkeypatch):
    from routers import pulse
    body = "Dyche is currently the Forest manager [1]."
    sources = [{"n": 1, "title": "t", "url": "u", "published": "2020-03-01", "content": "c"}]

    async def clean_auditor(messages):
        return '{"claims": []}', "coder", "cross-model (coder)"   # auditor finds nothing

    captured = {}

    async def revise_vera(messages, temperature=0.4):
        captured["revise"] = messages[-1]["content"]
        return "HEADLINE: Forest manager update\n\nThe manager has since changed [1]."

    monkeypatch.setattr(pulse, "_auditor", clean_auditor)
    monkeypatch.setattr(pulse, "_vera", revise_vera)
    errs = []
    h, b, stamp, info = asyncio.run(pulse.audit_claims("Forest", body, sources, errs, "Forest"))
    assert info["verdict"] == "revised"                            # deterministic date check forced a revision
    assert "Dyche is currently the Forest manager" in captured["revise"]


# --------------------------------------------------------------- cross-domain links


def test_cross_domain_links_active_engaged_neighbors_ranked():
    pg.upsert_node(id="seed", type="interest", label="hazelnuts", engagement=2.0, last_engaged=NOW)
    pg.upsert_node(id="oil", type="project", label="oil pressing", state="active",
                   engagement=3.0, last_engaged=NOW)
    pg.upsert_node(id="forage", type="interest", label="chicken forage",
                   engagement=1.0, last_engaged=NOW)
    pg.upsert_node(id="cold", type="interest", label="old hobby", engagement=0.05, last_engaged=NOW)
    pg.upsert_node(id="done", type="project", label="finished thing", state="resolved",
                   engagement=9.0, last_engaged=NOW)
    pg.add_edge("seed", "oil", "supports")
    pg.add_edge("forage", "seed", "related_to")
    pg.add_edge("seed", "cold", "related_to")
    pg.add_edge("seed", "done", "related_to")
    labels = [l["label"] for l in editor.cross_domain_links("seed", now=NOW)]
    assert "oil pressing" in labels and "chicken forage" in labels
    assert "old hobby" not in labels          # below engagement floor
    assert "finished thing" not in labels     # resolved node excluded
    assert labels.index("oil pressing") < labels.index("chicken forage")   # ranked by engagement


def test_connections_block_empty_when_no_links():
    assert editor.connections_block("Sam", []) == ""


def test_connections_block_lists_labels():
    block = editor.connections_block("Sam", [{"label": "oil pressing", "edge": "supports"}])
    assert "Sam" in block and "oil pressing" in block


def test_synthesis_prompt_includes_neighbor_labels():
    import time
    from routers import pulse
    t = int(time.time())   # _synthesis_user_prompt reads real now; seed fresh so engagement holds
    pg.upsert_node(id="seed", type="interest", label="hazelnuts", engagement=2.0, last_engaged=t)
    pg.upsert_node(id="oil", type="project", label="oil pressing", state="active",
                   engagement=3.0, last_engaged=t)
    pg.add_edge("seed", "oil", "supports")
    topic = {"title": "Hazelnut yields up", "angle": "harvest", "seed_node_id": "seed"}
    sources = [{"n": 1, "title": "t", "url": "u", "content": "c", "published": "2026-06-01"}]
    usr = pulse._synthesis_user_prompt(topic, sources, "Sam")
    assert "Hazelnut yields up" in usr and "oil pressing" in usr


def test_synthesis_prompt_without_seed_has_no_connections():
    from routers import pulse
    topic = {"title": "X", "angle": "y"}
    usr = pulse._synthesis_user_prompt(topic, [{"n": 1, "title": "t", "url": "u", "content": "c"}], "Sam")
    assert "Connections in" not in usr


# --------------------------------------------------------------- analyst-driven selection


def test_survivors_to_topics_maps_shape():
    pg.upsert_node(id="ai", type="interest", label="local LLMs", engagement=2.0, last_engaged=NOW)
    chosen = [{"title": "New TTS model", "finding_text": "a fast TTS", "url": "u",
               "seed_node_id": "ai", "classification": {"action": "try it on the 3090"}}]
    t = editor.survivors_to_topics(chosen)[0]
    assert t["title"] == "New TTS model" and t["seed_node_id"] == "ai"
    assert t["interest"] == "local LLMs"            # seed label drives the per-interest cap
    assert "3090" in t["angle"]


def test_select_topics_uses_analyst_when_graph_live(monkeypatch):
    import time
    from routers import pulse, scout, analyst
    pg.upsert_node(id="ai", type="interest", label="local LLMs", engagement=5.0,
                   last_engaged=int(time.time()))            # live: engagement above the scout floor

    async def fake_scout(*a, **k):
        return {"candidates": [{"finding_text": "x", "title": "x", "url": "u",
                                "published_date": None, "source": "news", "seed_node_id": "ai"}],
                "scouted_nodes": ["ai"], "skipped_sources": []}

    async def fake_rank(cands, **k):
        return {"chosen": [{"title": "New TTS", "finding_text": "f", "url": "u",
                            "seed_node_id": "ai", "classification": {}}], "considered": cands}

    monkeypatch.setattr(scout, "scout", fake_scout)
    monkeypatch.setattr(analyst, "rank", fake_rank)
    topics = asyncio.run(pulse._select_topics(0, who="Sam", persona=None, all_interests=[],
                                              memories=[], exclusions=[], want=8))
    assert [t["title"] for t in topics] == ["New TTS"]
    assert topics[0]["seed_node_id"] == "ai"
    # the Analyst delivers its ranked best once; later rounds add nothing
    assert asyncio.run(pulse._select_topics(1, who="Sam", persona=None, all_interests=[],
                                            memories=[], exclusions=[], want=8)) == []


def test_select_topics_falls_back_to_triage_when_graph_empty(monkeypatch):
    from routers import pulse
    seen = {}

    async def fake_triage(who, persona, interests, memories, exclusions, want, rnd):
        seen["called"] = True
        return [{"title": "v1 topic"}]

    monkeypatch.setattr(pulse, "_triage", fake_triage)
    topics = asyncio.run(pulse._select_topics(0, who="Sam", persona=None, all_interests=["x"],
                                              memories=[], exclusions=[], want=8))
    assert seen.get("called") and topics[0]["title"] == "v1 topic"
