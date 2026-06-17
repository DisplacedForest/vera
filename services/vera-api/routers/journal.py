"""Vera's Journal — a view over the Profile Graph's watch and project nodes.

GET /journal renders those nodes as the read-only journal the app shows; the node is the
source of truth (`editor.journal_view` builds the view, `editor.resolve_due` transitions a
watch to resolved on its condition + date). The document helpers below serve the legacy
self-authored file as a fallback until the graph holds watch/project nodes.
"""
import os
import re
import time

from fastapi import APIRouter
from pydantic import BaseModel

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
        # The legacy document carries no structured origin, so the fallback never claims
        # "requested": trustworthy provenance comes only from the graph's stamped
        # journal:origin fact (read by editor._origin).
        entries.append({"heading": heading, "slug": _slug(heading), "text": text,
                        "next_check": next_check, "origin": "self"})
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



# --------------------------------------------------------------------------- endpoints

class CommitRequest(BaseModel):
    text: str
    origin: str | None = None


@router.get("/journal", tags=["journal"])
async def get_journal(months: int = 1):
    """The journal as a view over the Profile Graph's watch/project nodes. Falls back to the
    legacy self-authored document while the graph holds no such nodes, so the app renders
    through the transition."""
    from . import editor
    view = editor.journal_view()
    if view["entries"] or view["archive"]:
        return view
    doc = read_document()
    entries = [{"heading": e["heading"], "slug": e["slug"], "text": e["text"],
                "next_check": e["next_check"], "origin": e["origin"]}
               for e in parse_document(doc)]
    return {"ok": True, "entries": entries, "raw": doc, "archive": read_archive(months)}


@router.post("/journal/commit", tags=["journal"])
async def commit(req: CommitRequest):
    """The owner asks her to watch something: land it as a watch node (cosine-folded onto an
    existing situation, or a new one)."""
    from . import editor
    label = (req.text or "").strip().splitlines()[0][:80] or "watch"
    nid = await editor.author_watch(label, facts=[req.text], origin=req.origin or "requested")
    return {"ok": True, "heading": label, "node_id": nid}
