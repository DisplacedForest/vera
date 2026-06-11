"""Vera's Journal — her standing commitments, kept as a self-authored continuity document.

The Journal is the second self-authored continuity document (HEARTBEAT.md, her standing
operating instructions, is the first). The shared contract:
  - the harness parses MINIMAL structure: `## ` entry boundaries and one `Next check:` line;
    everything else in the document is hers, in her words;
  - every source (a signals situation, a chat request, her own tick) writes only through
    her judgment — no code path appends mechanical entries;
  - the app renders the document read-only; the owner steers it by talking to her;
  - every touch logs a heartbeat outcome (journal_author / journal_check / journal_resolve)
    so her self-directed activity is auditable.

Entries are living briefs, not transcripts: she folds aging findings into a summary line on
rewrite, and resolution REMOVES the entry (to a monthly archive file that is cold storage,
never prompt context). Each heartbeat tick acts on a few due entries: the entry's own text
drives what checking means. The one checking capability today is a web-search-grounded
re-check; the loop's shape (act on the entry, append a dated finding, update the cadence
line) admits others without redesign.
"""
import json
import os
import re
import sqlite3
import time

from fastapi import APIRouter
from pydantic import BaseModel

from . import heartbeat_store as hb
from . import pulse_store as store
from . import vera_interests_store as vi
from .persona import orientation, owner, voiced
from .pulse import _inject, _vera
from .websearch import SearchRequest, search as web_search

router = APIRouter()

JOURNAL_PATH = os.environ.get("VERA_JOURNAL_PATH", "/data/journal/JOURNAL.md")
DUE_CAP = int(os.environ.get("JOURNAL_DUE_CAP", "3"))  # due entries acted on per tick


# --------------------------------------------------------------------------- document
# The harness's entire knowledge of the document's structure lives here.

_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{1,2}):(\d{2}))?\b")


def _parse_when(s):
    m = _DATE.search(s or "")
    if not m:
        return None
    y, mo, d, h, mi = m.groups()
    try:
        return time.mktime((int(y), int(mo), int(d), int(h or 0), int(mi or 0), 0, 0, 0, -1))
    except (ValueError, OverflowError):
        return None


def _slug(heading):
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-") or "entry"


def _sections(doc):
    """(preamble, [(heading, start, end)]) — char spans of each `## ` section."""
    starts = [m.start() for m in re.finditer(r"(?m)^## ", doc)]
    if not starts:
        return doc, []
    spans = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(doc)
        heading = doc[s:e].splitlines()[0][3:].strip()
        spans.append((heading, s, e))
    return doc[:starts[0]], spans


def document_preamble(doc):
    return _sections(doc)[0]


def parse_document(doc):
    """The entries, with the only structure the harness reads: heading/slug, the lenient
    `Next check:` time, and the origin class. `next_check` falls back to the latest date
    mentioned anywhere in the entry plus 24h (her last touch); an entry with no dates at
    all reads as never checked (next_check None == always due, first in line)."""
    entries = []
    for heading, s, e in _sections(doc)[1]:
        text = doc[s:e].rstrip("\n")
        nc_line = re.search(r"(?im)^\s*Next check:\s*(.+)$", text)
        next_check = _parse_when(nc_line.group(1)) if nc_line else None
        if next_check is None:
            rest = text[:nc_line.start()] + text[nc_line.end():] if nc_line else text
            dates = [_parse_when(m.group(0)) for m in _DATE.finditer(rest)]
            dates = [d for d in dates if d]
            if dates:
                next_check = max(dates) + 86400
        origin_line = re.search(r"(?im)^\s*Origin:\s*(.+)$", text)
        # Origin lines are in her words, so classification stays lenient: any owner/user
        # phrasing (or the configured owner's name) reads as a requested commitment.
        o = origin_line.group(1) if origin_line else ""
        requested = bool(origin_line and (
            re.search(r"\b(ask|request|owner|user)", o, re.I)
            or owner().lower() in o.lower()))
        entries.append({"heading": heading, "slug": _slug(heading), "text": text,
                        "next_check": next_check,
                        "origin": "requested" if requested else "self"})
    return entries


def due_entries(entries, now, cap=None):
    """Active entries due a check: never-checked (no dates anywhere) first, then oldest."""
    due = [e for e in entries if e["next_check"] is None or e["next_check"] <= now]
    due.sort(key=lambda e: (e["next_check"] is not None, e["next_check"] or 0))
    return due[:cap] if cap else due


def upsert_section(doc, section_text):
    """Replace one entry's section with her rewrite (matched by heading, case-insensitive),
    or append a new one. The model is never handed the whole file."""
    section_text = section_text.strip() + "\n"
    heading = section_text.splitlines()[0][3:].strip()
    for h, s, e in _sections(doc)[1]:
        if h.lower() == heading.lower():
            return doc[:s] + section_text + ("\n" if doc[e:].strip() else "") + doc[e:]
    base = doc.rstrip("\n")
    return (base + "\n\n" if base else "") + section_text


def remove_section(doc, heading):
    """(new document, removed section text or None)."""
    for h, s, e in _sections(doc)[1]:
        if h.lower() == heading.strip().lower():
            return (doc[:s] + doc[e:]).rstrip("\n") + "\n", doc[s:e].rstrip("\n")
    return doc, None


def archive_month(ts):
    return time.strftime("%Y-%m", time.localtime(ts))


def read_document():
    try:
        with open(JOURNAL_PATH, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def write_document(doc):
    os.makedirs(os.path.dirname(JOURNAL_PATH) or ".", exist_ok=True)
    tmp = JOURNAL_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(doc)
    os.replace(tmp, JOURNAL_PATH)


def _archive_dir():
    return os.path.join(os.path.dirname(JOURNAL_PATH) or ".", "archive")


def archive_entry(section_text, now=None):
    """Cold storage: append the resolved entry to the current month's rollover file.
    Never read back into any prompt; the heartbeat outcome log is the audit trail."""
    os.makedirs(_archive_dir(), exist_ok=True)
    path = os.path.join(_archive_dir(), f"{archive_month(now or time.time())}.md")
    with open(path, "a", encoding="utf-8") as f:
        f.write(section_text.rstrip("\n") + "\n\n")


def read_archive(months=1):
    out = []
    try:
        names = sorted(n for n in os.listdir(_archive_dir()) if n.endswith(".md"))
    except OSError:
        return out
    for name in names[-months:]:
        try:
            with open(os.path.join(_archive_dir(), name), encoding="utf-8") as f:
                out.append({"month": name[:-3], "text": f.read()})
        except OSError:
            continue
    return out


# --------------------------------------------------------------------------- her voice

def _conventions():
    return (
        "You keep a Journal of your standing commitments: things you have decided to keep "
        "monitoring or following through on. Each commitment is ONE `## ` section in your own "
        "words, and it is a compact living brief, never a transcript. A good entry carries: why "
        "it matters to this household, what you are checking, what would resolve it, an "
        "`Origin:` line naming where the commitment came from (keep the origin wording you "
        "were given, especially who asked), a `Next check:` line "
        "(YYYY-MM-DD or YYYY-MM-DD HH:MM) set to when it genuinely needs another look, and a "
        "short log of dated finding lines like `- YYYY-MM-DD: ...`. When the log ages, fold "
        "older lines into one summary line. What matters to this household: anything that would "
        f"plausibly {orientation()}."
    )


AUTHOR_TASK = (
    "{conventions}\n\n"
    "New material has arrived. Decide whether it belongs to one of your existing commitments "
    "(the same real-world situation, even under different wording) or deserves a new entry. "
    "Output EXACTLY this format and nothing else:\n"
    "FOLD: <the exact existing heading it belongs to, or NEW>\n"
    "===\n"
    "## <heading>\n"
    "<the complete entry section, updated or new>\n\n"
    "If the material does not deserve a standing commitment at all, output exactly SKIP."
)

CHECK_QUERY_TASK = (
    "Read your journal entry below and output ONLY the single best web search query to check "
    "on it right now. No quotes, no prose, just the query."
)

GROUND_TASK = (
    "Synthesize what the numbered sources say about the topic in 1-3 sentences, citing them "
    "inline as [n]. Use ONLY what the sources support; never invent specifics. If the sources "
    "say nothing useful about it, output exactly NOTHING."
)

RECHECK_TASK = (
    "{conventions}\n\n"
    "This is a due check on ONE entry. Given the entry and the latest grounded finding, "
    "rewrite the COMPLETE section: append a dated finding line for today (folding aging "
    "lines), set `Next check:` to when it genuinely needs another look, and keep it a compact "
    "brief. Judge two things against the entry's own words: did something MATERIALLY change "
    "(a real development that would plausibly {orientation}, not rewording or noise; be "
    "conservative, when in doubt no)? And is the entry's stated resolve condition now met?\n"
    "Output EXACTLY this format and nothing else:\n"
    "CHANGED: yes|no\n"
    "WHAT: <one concrete sentence on what is newly different, empty if nothing>\n"
    "RESOLVED: yes|no\n"
    "===\n"
    "## <heading>\n"
    "<the complete updated section; when resolved, end with a one-line closing note instead "
    "of a Next check line>"
)


def _split_verdict(raw):
    """(header_fields, section_text) from a VERDICT/===/section reply; lenient."""
    head, sep, rest = raw.partition("===")
    fields = {}
    for key in ("FOLD", "CHANGED", "WHAT", "RESOLVED"):
        m = re.search(rf"(?im)^{key}:\s*(.*)$", head)
        if m:
            fields[key] = m.group(1).strip()
    section = rest.strip() if sep else ""
    if section and not section.startswith("## "):
        m = re.search(r"(?m)^## ", section)
        section = section[m.start():] if m else ""
    return fields, section


async def author(material, origin):
    """The single write path every source uses: handed her active journal and new material,
    she folds it into the situation it belongs to or authors a new entry. Returns the entry
    heading, or None when she judges it unworthy of a standing commitment."""
    doc = read_document()
    today = time.strftime("%Y-%m-%d")
    usr = (f"Today: {today}\nOrigin to record: {origin}\n\n"
           f"## Your active journal\n{doc.strip() or '(empty)'}\n\n## New material\n{material}")
    raw = (await _vera([{"role": "system",
                         "content": voiced(AUTHOR_TASK.format(conventions=_conventions()))},
                        {"role": "user", "content": usr}], temperature=0.4)).strip()
    if raw.upper().startswith("SKIP"):
        return None
    fields, section = _split_verdict(raw)
    if not section:
        return None
    heading = section.splitlines()[0][3:].strip()
    fold = fields.get("FOLD", "NEW")
    doc = read_document()  # re-read: the author call is slow, the file may have moved
    if fold.upper() != "NEW" and fold.lower() != heading.lower():
        doc, _ = remove_section(doc, fold)  # she renamed the situation; retire the old heading
    write_document(upsert_section(doc, section))
    hb.log("journal_author", heading, extra={"origin": origin, "folded": fold.upper() != "NEW"})
    return heading


# --------------------------------------------------------------------------- the tick step

async def _ground(heading, results):
    srcs = [{"n": i + 1, "title": getattr(x, "title", "") or getattr(x, "url", ""),
             "url": getattr(x, "url", ""), "content": getattr(x, "content", "")}
            for i, x in enumerate(results) if getattr(x, "url", None)]
    if not srcs:
        return None, []
    corpus = "\n\n".join(f"[{s['n']}] {s['title']}\n{s['content']}" for s in srcs)[:6000]
    text = (await _vera([{"role": "system", "content": GROUND_TASK},
                         {"role": "user", "content": f"Topic: {heading}\n\nNumbered sources:\n{corpus}"}],
                        temperature=0.2)).strip()
    if not text or text.upper().strip(".!\"' ").startswith("NOTHING"):
        return None, []
    cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", text)})
    urls = [s["url"] for s in srcs if s["n"] in cited] or [s["url"] for s in srcs[:3]]
    return text, urls


async def _update_card(entry, finding, what, urls):
    """One 'Watch update' Pulse card per entry, refreshed in place so a re-firing
    commitment never stacks. category=watch:<slug> keeps the signals rebuild off it."""
    title = f"Watch update · {entry['heading']}"[:80]
    body = f"{finding}\n\n**What changed:** {what}" if what else finding
    sources = [{"n": i + 1, "title": u, "url": u} for i, u in enumerate(urls or [])]
    cat = f"watch:{entry['slug']}"
    existing = sorted([c for c in store.list_cards()
                       if c.get("kind") == "signals" and c.get("category") == cat
                       and c.get("status") in ("new", "seen")],
                      key=lambda c: c.get("created_at") or 0, reverse=True)
    if existing:
        store.insert_card({**existing[0], "status": "new", "title": title, "body": body,
                           "summary": (what or finding)[:200], "sources": sources,
                           "severity": "notice", "category": cat})
    else:
        await _inject(title, body, kind="signals", severity="notice", sources=sources,
                      summary=(what or finding)[:200], category=cat)


async def _closing_card(entry, what):
    """A requested commitment closes loudly: the owner asked for this watch, so its
    resolution is surfaced rather than retired in silence."""
    title = f"Watch closed · {entry['heading']}"[:80]
    body = (what or "The situation this was tracking has resolved.") + \
        "\n\nYou asked me to keep an eye on this; it is off my journal now."
    await _inject(title, body, kind="signals", severity="notice",
                  summary=body.splitlines()[0][:200], category=f"watch:{entry['slug']}")


async def _check_entry(entry):
    """One due entry, end to end: derive the check from the entry's own text, ground it,
    let her rewrite the section, then apply her verdicts (change → card, resolved → archive)."""
    query = (await _vera([{"role": "system", "content": voiced(CHECK_QUERY_TASK)},
                          {"role": "user", "content": entry["text"]}],
                         temperature=0.2)).strip().strip('"').splitlines()[0]
    finding, urls = None, []
    if query:
        res = await web_search(SearchRequest(query=query, fetch_pages=2, max_results=5))
        finding, urls = await _ground(entry["heading"], res.results)
    today = time.strftime("%Y-%m-%d")
    usr = (f"Today: {today}\n\n## The entry\n{entry['text']}\n\n"
           f"## Latest grounded finding\n{finding or '(nothing reliable found this check)'}")
    raw = (await _vera([{"role": "system", "content": voiced(
        RECHECK_TASK.format(conventions=_conventions(), orientation=orientation()))},
        {"role": "user", "content": usr}], temperature=0.3)).strip()
    fields, section = _split_verdict(raw)
    if not section:
        raise ValueError("recheck reply had no section")
    changed = fields.get("CHANGED", "no").lower().startswith("y") and bool(finding)
    resolved = fields.get("RESOLVED", "no").lower().startswith("y")
    what = fields.get("WHAT") or None
    doc = read_document()
    if resolved:
        doc, _ = remove_section(doc, entry["heading"])
        new_heading = section.splitlines()[0][3:].strip()
        if new_heading.lower() != entry["heading"].lower():
            doc, _ = remove_section(doc, new_heading)
        write_document(doc)
        archive_entry(section)
        hb.log("journal_resolve", entry["heading"], extra={"origin": entry["origin"]})
        if entry["origin"] == "requested":
            await _closing_card(entry, what or finding)
    else:
        write_document(upsert_section(doc, section))
        hb.log("journal_check", entry["heading"], extra={"changed": changed, "query": query})
        if changed:
            await _update_card(entry, finding, what, urls)
    return {"changed": changed, "resolved": resolved}


async def tick_step(errors):
    """The heartbeat's journal step: act on a few due commitments. Quiet is success.
    Returns the headings whose check surfaced something."""
    fired = []
    try:
        # Legacy watch rows fold in here — a cheap no-op once they're gone, and the retry
        # path for any group whose authoring failed on an earlier attempt.
        await migrate_legacy_watches()
        entries = parse_document(read_document())
        if not entries:
            return fired
        for e in due_entries(entries, now=time.time(), cap=DUE_CAP):
            try:
                res = await _check_entry(e)
                if res["changed"] or res["resolved"]:
                    fired.append(e["heading"])
            except Exception as ex:
                errors.append(f"journal {e['heading']}: {ex}")
    except Exception as ex:
        errors.append(f"journal: {ex}")
    return fired


# --------------------------------------------------------------------------- legacy watches

async def migrate_legacy_watches():
    """Hand the interests store's mechanical watch rows to her authoring pass so they
    collapse into per-situation journal entries. Rows are deleted per GROUP and only after
    that group's authoring succeeds — a failed group stays in the store for the next
    attempt. True if anything migrated. Safe on stores that never grew the watch columns."""
    try:
        c = sqlite3.connect(vi.DB_PATH)
        c.row_factory = sqlite3.Row
        rows = c.execute("SELECT id, topic, watch_query, origin, last_finding FROM interest "
                         "WHERE COALESCE(is_watch,0)=1").fetchall()
    except sqlite3.Error:
        return False
    if not rows:
        c.close()
        return False
    groups = {}
    for r in rows:
        groups.setdefault(r["origin"] or r["topic"], []).append(r)
    migrated = False
    for origin_label, group in groups.items():
        lines = [f"- {r['topic']}" + (f": last known {r['last_finding']}" if r["last_finding"] else "")
                 for r in group]
        material = (f"Standing things you were already monitoring about \"{origin_label}\" "
                    "(from your old watch list):\n" + "\n".join(lines))
        try:
            await author(material, origin=f"carried over from watches: {origin_label}")
        except Exception:
            continue  # this group's rows stay for the next attempt
        with c:
            c.executemany("DELETE FROM interest WHERE id=?", [(r["id"],) for r in group])
        migrated = True
    c.close()
    return migrated and os.path.exists(JOURNAL_PATH)


# --------------------------------------------------------------------------- endpoints

class CommitRequest(BaseModel):
    text: str
    origin: str | None = None


@router.get("/journal", tags=["journal"])
async def get_journal(months: int = 1):
    doc = read_document()
    entries = [{"heading": e["heading"], "slug": e["slug"], "text": e["text"],
                "next_check": e["next_check"], "origin": e["origin"]}
               for e in parse_document(doc)]
    return {"ok": True, "entries": entries, "raw": doc, "archive": read_archive(months)}


@router.post("/journal/commit", tags=["journal"])
async def commit(req: CommitRequest):
    origin = req.origin or f"{owner()} asked ({time.strftime('%Y-%m-%d')})"
    heading = await author(req.text, origin=origin)
    if not heading:
        return {"ok": True, "skipped": True}
    return {"ok": True, "heading": heading}


@router.post("/journal/migrate", tags=["journal"])
async def migrate():
    return {"ok": True, "migrated": await migrate_legacy_watches()}
