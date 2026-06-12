"""Pulse store — SQLite-backed, the standalone home for Pulse cards.

A card is a typed record with real lifecycle status (not an OWUI folder chat); the app
reads the feed from here and cards only cross into OWUI at promotion.
"""

import json
import os
import sqlite3
import time

DB_PATH = os.environ.get("PULSE_DB_PATH", "/data/pulse.db")
# Household owner's OWUI user id — pre-existing cards and any user-unscoped call default to it.
# Unset means unscoped cards stay unscoped; set it to your OWUI account's UUID for per-user feeds.
DEFAULT_USER = os.environ.get("VERA_DEFAULT_USER", "")

ACTIVE = ("new", "seen", "bookmarked", "promoted")  # shown in the feed (not expired)


def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS cards (
                id TEXT PRIMARY KEY,
                created_at INTEGER,
                day TEXT,
                status TEXT,
                title TEXT,
                summary TEXT,
                body TEXT,
                image_url TEXT,
                tint TEXT,
                sources TEXT,          -- json [{n,title,url}]
                inline_images TEXT,    -- json [{n,url,caption,sourceN}]
                promoted_chat_id TEXT
            )
            """
        )
        # migrations: add later columns if this is a pre-existing cards table
        cols = [r[1] for r in c.execute("PRAGMA table_info(cards)").fetchall()]
        if "action" not in cols:
            # json {verb,args,risk,reversible,preview,token}
            c.execute("ALTER TABLE cards ADD COLUMN action TEXT")
        if "kind" not in cols:
            # card type — "research" (default) | "status" | "weather" | "health" | …
            c.execute("ALTER TABLE cards ADD COLUMN kind TEXT")
        if "severity" not in cols:
            # ambient-card severity — "notice" | "alert" | "critical" (null = neutral)
            c.execute("ALTER TABLE cards ADD COLUMN severity TEXT")
        if "user_id" not in cols:
            # the person this card is FOR. Backfill pre-existing cards to the household owner.
            c.execute("ALTER TABLE cards ADD COLUMN user_id TEXT")
            c.execute("UPDATE cards SET user_id=? WHERE user_id IS NULL", (DEFAULT_USER,))
        if "provenance" not in cols:
            # how the card was triggered — "scheduled" (pulse/run) | "heartbeat" (for-you).
            c.execute("ALTER TABLE cards ADD COLUMN provenance TEXT")
            c.execute("UPDATE cards SET provenance='scheduled' WHERE provenance IS NULL")
        if "category" not in cols:
            # System-vein sub-grouping for status cards — vera | infra | health | update.
            c.execute("ALTER TABLE cards ADD COLUMN category TEXT")
        if "change_set" not in cols:
            # reversible memory-tending diff (json) — what merged/forgot/promoted, with full
            # before-snapshots so the System detail can show it and offer restore/undo.
            c.execute("ALTER TABLE cards ADD COLUMN change_set TEXT")
        if "audit" not in cols:
            # claim-audit provenance for research cards — "cross-model (<model>)" |
            # "self (fallback)" | "none" (null = card kind that is never audited).
            c.execute("ALTER TABLE cards ADD COLUMN audit TEXT")
        if "items" not in cols:
            # multi-item action card (digest) — json list of per-row items, each with its own
            # staged action token + state (pending|approved|skipped). Drives the approve/skip card UI.
            c.execute("ALTER TABLE cards ADD COLUMN items TEXT")
        # Per-event read receipts — separate from the card, because ambient vein events are
        # one shared mind but read independently per person. Composite PK = idempotent writes.
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS pulse_reads (
                user_id TEXT,
                card_id TEXT,
                read_at INTEGER,
                PRIMARY KEY (user_id, card_id)
            )
            """
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_reads_user ON pulse_reads(user_id)")


def insert_card(card: dict):
    init()
    with _conn() as c:
        c.execute(
            """INSERT OR REPLACE INTO cards
               (id, created_at, day, status, title, summary, body, image_url, tint, sources, inline_images, promoted_chat_id, action, kind, severity, user_id, provenance, category, change_set, items, audit)
               VALUES (:id,:created_at,:day,:status,:title,:summary,:body,:image_url,:tint,:sources,:inline_images,:promoted_chat_id,:action,:kind,:severity,:user_id,:provenance,:category,:change_set,:items,:audit)""",
            {
                "id": card["id"],
                "created_at": card.get("created_at") or int(time.time()),
                "day": card["day"],
                "status": card.get("status", "new"),
                "title": card.get("title", ""),
                "summary": card.get("summary", ""),
                "body": card.get("body", ""),
                "image_url": card.get("image_url"),
                "tint": card.get("tint"),
                "sources": json.dumps(card.get("sources") or []),
                "inline_images": json.dumps(card.get("inline_images") or []),
                "promoted_chat_id": card.get("promoted_chat_id"),
                "action": json.dumps(card["action"]) if card.get("action") else None,
                "kind": card.get("kind") or "research",
                "severity": card.get("severity"),
                "user_id": card.get("user_id") or DEFAULT_USER,
                "provenance": card.get("provenance") or "scheduled",
                "category": card.get("category"),
                "change_set": json.dumps(card["change_set"]) if card.get("change_set") else None,
                "items": json.dumps(card["items"]) if card.get("items") else None,
                "audit": card.get("audit"),
            },
        )


def _row_to_card(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "created_at": r["created_at"],
        "day": r["day"],
        "status": r["status"],
        "title": r["title"],
        "summary": r["summary"] or "",
        "body": r["body"] or "",
        "image_url": r["image_url"],
        "tint": r["tint"],
        "sources": json.loads(r["sources"] or "[]"),
        "inline_images": json.loads(r["inline_images"] or "[]"),
        "promoted_chat_id": r["promoted_chat_id"],
        "action": json.loads(r["action"]) if r["action"] else None,
        "kind": r["kind"] or "research",
        "severity": r["severity"],
        "user_id": r["user_id"] or DEFAULT_USER,
        "provenance": (r["provenance"] if "provenance" in r.keys() else None) or "scheduled",
        "category": r["category"] if "category" in r.keys() else None,
        "change_set": json.loads(r["change_set"]) if ("change_set" in r.keys() and r["change_set"]) else [],
        "items": json.loads(r["items"]) if ("items" in r.keys() and r["items"]) else [],
        "audit": r["audit"] if "audit" in r.keys() else None,
    }


def list_cards(include_expired: bool = False, user_id: str | None = None) -> list[dict]:
    """The feed. If user_id is given, return only that person's cards; otherwise all."""
    init()
    where, args = [], []
    if not include_expired:
        where.append(f"status IN ({','.join('?' * len(ACTIVE))})")
        args += list(ACTIVE)
    if user_id:
        where.append("user_id = ?")
        args.append(user_id)
    sql = "SELECT * FROM cards" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created_at DESC"
    with _conn() as c:
        rows = c.execute(sql, args).fetchall()
    return [_row_to_card(r) for r in rows]


def get_card(card_id: str) -> dict | None:
    init()
    with _conn() as c:
        r = c.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    return _row_to_card(r) if r else None


def set_status(card_id: str, status: str, promoted_chat_id: str | None = None):
    init()
    with _conn() as c:
        if promoted_chat_id is not None:
            c.execute("UPDATE cards SET status=?, promoted_chat_id=? WHERE id=?", (status, promoted_chat_id, card_id))
        else:
            c.execute("UPDATE cards SET status=? WHERE id=?", (status, card_id))


def apply_audit(card_id: str, title: str, body: str, audit: str):
    """Apply the end-of-run claim audit's outcome to a stored card: any title/body
    revision plus the provenance stamp recording how the claims were checked."""
    init()
    with _conn() as c:
        c.execute("UPDATE cards SET title=?, body=?, audit=? WHERE id=?",
                  (title, body, audit, card_id))


def delete_card(card_id: str):
    init()
    with _conn() as c:
        c.execute("DELETE FROM cards WHERE id=?", (card_id,))


def set_items(card_id: str, items: list):
    """Persist the digest card's items array after a per-item decision."""
    init()
    with _conn() as c:
        c.execute("UPDATE cards SET items=? WHERE id=?", (json.dumps(items or []), card_id))


# ---- per-event read state ----

_SEV_RANK = {"critical": 3, "alert": 2, "notice": 1}


def _sev_rank(s) -> int:
    return _SEV_RANK.get(s or "", 0)


def mark_read(user_id: str, card_id: str):
    """Record that this person opened this card's detail. Idempotent (composite PK)."""
    init()
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO pulse_reads(user_id, card_id, read_at) VALUES (?,?,?)",
                  (user_id, card_id, int(time.time())))


def read_ids(user_id: str) -> set:
    """The set of card ids this person has read (for annotating the feed + vein overlay rows)."""
    init()
    with _conn() as c:
        rows = c.execute("SELECT card_id FROM pulse_reads WHERE user_id=?", (user_id,)).fetchall()
    return {r["card_id"] for r in rows}


def unread_counts(user_id: str) -> dict:
    """Per kind, this person's UNREAD count and the max unread severity (drives the chip dot color).
    Counts only active cards with no receipt for this user."""
    init()
    with _conn() as c:
        rows = c.execute(
            f"""SELECT c.kind AS kind, c.severity AS severity
                FROM cards c LEFT JOIN pulse_reads r ON r.card_id = c.id AND r.user_id = ?
                WHERE c.user_id = ? AND c.status IN ({','.join('?' * len(ACTIVE))}) AND r.card_id IS NULL""",
            (user_id, user_id, *ACTIVE),
        ).fetchall()
    agg: dict = {}
    for r in rows:
        d = agg.setdefault(r["kind"] or "research", {"unread": 0, "max_severity": None})
        d["unread"] += 1
        if _sev_rank(r["severity"]) > _sev_rank(d["max_severity"]):
            d["max_severity"] = r["severity"]
    return agg


def sweep(today: str) -> int:
    """Expire ALL prior-day cards from the feed. Bookmarked/promoted cards already live on as real
    OWUI chats (graduation); the Pulse feed itself is daily and clears overnight. Returns count expired."""
    init()
    with _conn() as c:
        cur = c.execute(
            "UPDATE cards SET status='expired' WHERE status != 'expired' AND day < ?",
            (today,),
        )
        return cur.rowcount


# ---- async run status ----
# A single-row record of the in-flight / last Pulse run, so /pulse/run can return 202 immediately
# and callers poll /pulse/run_status to completion instead of holding a 10-minute HTTP call.

RUN_STALE_SECS = int(os.environ.get("PULSE_RUN_STALE_SECS", "1800"))  # a 'running' row older than this == dead
# Process-start marker. A 'running' row whose run began BEFORE this process started means the
# vera-api process restarted mid-run — the background task died with the old process, so the row is a
# zombie. We mark it stale immediately on read, instead of waiting out RUN_STALE_SECS.
_PROC_START = int(time.time())


def _ensure_run_status():
    with _conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS pulse_run_status (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT)")


def set_run_status(d: dict):
    """Upsert the single run-status row (full replace). Caller owns the dict shape:
    {run_id, state, kind, started_at, finished_at, topics, injected, errors}."""
    _ensure_run_status()
    with _conn() as c:
        c.execute("INSERT INTO pulse_run_status(id, data) VALUES(1, ?) "
                  "ON CONFLICT(id) DO UPDATE SET data=excluded.data", (json.dumps(d),))


def get_run_status() -> dict:
    """The current/last run status, with the stale override applied. A 'running' row with no
    finished_at is reported as 'stale' when EITHER it's older than RUN_STALE_SECS, OR it began before
    this process started (a mid-run vera-api restart orphaned the background task)."""
    _ensure_run_status()
    with _conn() as c:
        row = c.execute("SELECT data FROM pulse_run_status WHERE id=1").fetchone()
    if not row or not row["data"]:
        return {"state": "idle"}
    d = json.loads(row["data"])
    if d.get("state") == "running" and not d.get("finished_at"):
        started = int(d.get("started_at") or 0)
        if started < _PROC_START or int(time.time()) - started > RUN_STALE_SECS:
            d = {**d, "state": "stale"}
    return d
