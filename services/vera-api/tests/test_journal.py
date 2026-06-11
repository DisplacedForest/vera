"""Journal document mechanics — the minimal structure the harness owns: `## ` entry
boundaries, the lenient `Next check:` line, origin classification, section-scoped
writes, and archive month naming. Everything else in the document is hers and is
deliberately NOT parsed. Run under pytest."""
import time

from routers import journal


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
