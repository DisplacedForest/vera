"""Journal document mechanics — the minimal structure the harness owns: `## ` entry
boundaries, the lenient `Next check:` line, origin classification, section-scoped
writes, archive month naming, and legacy-watch migration safety. Everything else in
the document is hers and is deliberately NOT parsed. Run under pytest."""
import asyncio
import sqlite3
import time

from routers import journal, vera_interests_store as vi


DOC = """# Journal

## Strait of Hormuz closure
Origin: signals — Hormuz trip
Why: kinetic conflict halted tanker traffic; fuel, fertilizer and food ride that lane.
Resolve when: the strait reopens to commercial traffic.
Next check: 2026-06-12
- 2026-06-10: traffic near standstill; diesel +4% w/w.
- 2026-06-11: ceasefire talks announced, no traffic change yet.

## Lumber prices
Origin: Zachary asked (2026-06-11)
Watching for a material move before the deck project.
Next check: 2026-06-13 09:00

## A fresh commitment
No dates anywhere in this one yet.
"""

T_2026_06_11 = time.mktime((2026, 6, 11, 12, 0, 0, 0, 0, -1))
T_2026_06_13 = time.mktime((2026, 6, 13, 12, 0, 0, 0, 0, -1))


# --------------------------------------------------------------------------- parsing

def test_parse_finds_all_entries_and_preserves_preamble():
    entries = journal.parse_document(DOC)
    assert [e["heading"] for e in entries] == [
        "Strait of Hormuz closure", "Lumber prices", "A fresh commitment"]
    assert journal.document_preamble(DOC).strip() == "# Journal"


def test_entry_text_is_the_full_section():
    e = journal.parse_document(DOC)[0]
    assert e["text"].startswith("## Strait of Hormuz closure")
    assert "diesel +4%" in e["text"]
    assert "Lumber" not in e["text"]


def test_next_check_parses_date_and_datetime():
    entries = journal.parse_document(DOC)
    hormuz, lumber, fresh = entries
    assert time.localtime(hormuz["next_check"])[:3] == (2026, 6, 12)
    assert time.localtime(lumber["next_check"])[:4] == (2026, 6, 13, 9)
    assert fresh["next_check"] is None


def test_origin_classification_is_lenient():
    entries = journal.parse_document(DOC)
    assert entries[0]["origin"] == "self"        # signals-born
    assert entries[1]["origin"] == "requested"   # "asked"
    assert entries[2]["origin"] == "self"        # no Origin line


def test_origin_survives_her_paraphrase():
    # She rewrites Origin lines in her own words; owner/user phrasings still classify
    # as requested, while signals- and migration-born wordings stay self.
    doc = ("## A\nOrigin: New commitment based on user-provided watch list.\n\n"
           "## B\nOrigin: the owner wanted this tracked.\n\n"
           "## C\nOrigin: carried over from watches: Hormuz traffic.\n\n"
           "## D\nOrigin: signals situation, helicopter incident.\n")
    a, b, c, d = journal.parse_document(doc)
    assert a["origin"] == "requested"
    assert b["origin"] == "requested"
    assert c["origin"] == "self"
    assert d["origin"] == "self"


def test_origin_matches_configured_owner_name(monkeypatch):
    monkeypatch.setattr(journal, "owner", lambda: "Alex")
    doc = "## A\nOrigin: Alex, 2026-06-11.\n\n## B\nOrigin: routine self check.\n"
    a, b = journal.parse_document(doc)
    assert a["origin"] == "requested"
    assert b["origin"] == "self"


def test_slug_is_stable_and_kebab():
    e = journal.parse_document(DOC)[0]
    assert e["slug"] == "strait-of-hormuz-closure"


# --------------------------------------------------------------------------- dueness

def test_due_respects_next_check():
    entries = journal.parse_document(DOC)
    due = journal.due_entries(entries, now=T_2026_06_11)
    # Hormuz is due 06-12, lumber 06-13: neither due on 06-11; the dateless entry is.
    assert [e["heading"] for e in due] == ["A fresh commitment"]
    due_later = journal.due_entries(entries, now=T_2026_06_13)
    assert {e["heading"] for e in due_later} == {
        "Strait of Hormuz closure", "Lumber prices", "A fresh commitment"}


def test_missing_next_check_falls_back_to_latest_dated_line_plus_24h():
    doc = ("## Quiet thing\n- 2026-06-10: baseline noted.\n")
    e = journal.parse_document(doc)[0]
    assert e["next_check"] is not None
    assert not journal.due_entries([e], now=time.mktime((2026, 6, 10, 18, 0, 0, 0, 0, -1)))
    assert journal.due_entries([e], now=T_2026_06_11 + 86400)


def test_due_cap_orders_never_checked_first():
    entries = journal.parse_document(DOC)
    due = journal.due_entries(entries, now=T_2026_06_13, cap=2)
    assert len(due) == 2
    assert due[0]["heading"] == "A fresh commitment"  # dateless = never checked


# --------------------------------------------------------------------------- section writes

def test_upsert_replaces_one_section_in_place():
    new = "## Strait of Hormuz closure\nRewritten body.\nNext check: 2026-06-14\n"
    out = journal.upsert_section(DOC, new)
    assert "Rewritten body." in out
    assert "diesel +4%" not in out
    assert "Lumber prices" in out and "A fresh commitment" in out
    assert journal.document_preamble(out).strip() == "# Journal"


def test_upsert_appends_unknown_section():
    out = journal.upsert_section(DOC, "## Brand new\nBody.\n")
    heads = [e["heading"] for e in journal.parse_document(out)]
    assert heads[-1] == "Brand new" and len(heads) == 4


def test_remove_section_returns_removed_text():
    out, removed = journal.remove_section(DOC, "Lumber prices")
    assert removed.startswith("## Lumber prices")
    assert "Lumber prices" not in out
    assert len(journal.parse_document(out)) == 2


def test_remove_unknown_section_is_a_noop():
    out, removed = journal.remove_section(DOC, "Nope")
    assert removed is None and out == DOC


# --------------------------------------------------------------------------- archive

def test_archive_month_naming():
    assert journal.archive_month(T_2026_06_11) == "2026-06"


def test_empty_document_parses_to_nothing():
    assert journal.parse_document("") == []
    assert journal.due_entries([], now=T_2026_06_11) == []


# --------------------------------------------------------------------------- migration

def test_failed_migration_group_stays_for_retry(monkeypatch, tmp_path):
    """Rows are deleted per group, only after that group's authoring succeeds — a group
    whose author pass fails survives in the store for the next attempt."""
    monkeypatch.setattr(journal, "JOURNAL_PATH", str(tmp_path / "JOURNAL.md"))
    vi.init()
    with sqlite3.connect(vi.DB_PATH) as c:
        c.execute("DELETE FROM interest WHERE COALESCE(is_watch,0)=1")
        for topic, origin in (("oil prices", "Hormuz"), ("tanker traffic", "Hormuz"),
                              ("aftershocks", "Quake")):
            c.execute("INSERT INTO interest(id,topic,is_watch,origin,salience,source) "
                      "VALUES(?,?,1,?,0.1,'watch')", (topic, topic, origin))

    async def fake_author(material, origin):
        if "Quake" in origin:
            raise RuntimeError("model unreachable")
        journal.write_document(journal.upsert_section(
            journal.read_document(), f"## Hormuz situation\n{origin}\n"))
        return "Hormuz situation"

    monkeypatch.setattr(journal, "author", fake_author)
    assert asyncio.run(journal.migrate_legacy_watches()) is True
    with sqlite3.connect(vi.DB_PATH) as c:
        left = [r[0] for r in c.execute(
            "SELECT topic FROM interest WHERE COALESCE(is_watch,0)=1").fetchall()]
    assert left == ["aftershocks"]   # the failed Quake group survives; Hormuz rows are gone

    # the surviving group migrates cleanly once authoring recovers
    async def ok_author(material, origin):
        journal.write_document(journal.upsert_section(
            journal.read_document(), f"## Quake situation\n{origin}\n"))
        return "Quake situation"
    monkeypatch.setattr(journal, "author", ok_author)
    assert asyncio.run(journal.migrate_legacy_watches()) is True
    with sqlite3.connect(vi.DB_PATH) as c:
        assert c.execute("SELECT COUNT(*) FROM interest WHERE COALESCE(is_watch,0)=1").fetchone()[0] == 0
