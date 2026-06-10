"""Home-model store — the inspectable "here's what your house does" model.

SQLite at /data/home_model.db. Holds the patterns derived by home_model_mine from the home_events
window, each with its REAL specifics (spec_json), a consistency score, and the already-automated
tag. The model IS the current re-mining of the retained window, so a refresh replaces the whole
generation atomically (no decay, no incremental merge — re-deriving from raw is trivial).
Mirrors the pulse/knowledge/home_events store pattern.
"""
import json
import os
import sqlite3
import time

DB_PATH = os.environ.get("HOME_MODEL_DB_PATH", "/data/home_model.db")


def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT,             -- temporal | sequence | conditional | numeric
                entity_id TEXT,        -- the subject entity
                peer_id TEXT,          -- the correlated/conditioning entity (sequence/conditional), or NULL
                spec TEXT,             -- json: the REAL specifics (times, lags, conditions, curve)
                consistency REAL,      -- how reliably it holds, 0..1
                score REAL,            -- ranking score (consistency, lightly shaped per kind)
                support_k INTEGER,     -- observed occurrences / days in support
                support_n INTEGER,     -- eligible occurrences / days (the denominator)
                automated INTEGER,     -- 1 = explained by a ~/ha automation/script, 0 = emergent candidate
                automation_ref TEXT,   -- which automation/script explains it (alias), or NULL
                evidence TEXT,         -- how we know it's automated: context-linked | config-match | NULL
                narration TEXT,        -- plain-language restatement (LLM, grounded), or NULL
                mined_at INTEGER
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mined_at INTEGER,
                window_start INTEGER,
                window_end INTEGER,
                events_scanned INTEGER,
                n_patterns INTEGER,
                n_automated INTEGER,
                n_candidate INTEGER
            )
            """
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_pat_kind ON patterns(kind, score DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_pat_entity ON patterns(entity_id)")


def replace_patterns(patterns: list[dict], mined_at: int | None = None) -> int:
    """Atomically swap in a fresh generation: clear the table, insert this run's patterns."""
    init()
    ts = mined_at or int(time.time())
    with _conn() as c:
        c.execute("DELETE FROM patterns")
        for p in patterns:
            c.execute(
                """INSERT INTO patterns(kind, entity_id, peer_id, spec, consistency, score,
                       support_k, support_n, automated, automation_ref, evidence, narration, mined_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    p.get("kind"), p.get("entity_id"), p.get("peer_id"),
                    json.dumps(p.get("spec")), p.get("consistency"), p.get("score"),
                    p.get("support_k"), p.get("support_n"),
                    1 if p.get("automated") else 0, p.get("automation_ref"), p.get("evidence"),
                    p.get("narration"), ts,
                ),
            )
    return len(patterns)


def record_run(meta: dict, mined_at: int | None = None):
    init()
    ts = mined_at or int(time.time())
    with _conn() as c:
        c.execute(
            """INSERT INTO runs(mined_at, window_start, window_end, events_scanned,
                   n_patterns, n_automated, n_candidate) VALUES (?,?,?,?,?,?,?)""",
            (ts, meta.get("window_start"), meta.get("window_end"), meta.get("events_scanned"),
             meta.get("n_patterns"), meta.get("n_automated"), meta.get("n_candidate")),
        )


def _row(r) -> dict:
    return {
        "id": r["id"], "kind": r["kind"], "entity_id": r["entity_id"], "peer_id": r["peer_id"],
        "spec": json.loads(r["spec"]) if r["spec"] else None,
        "consistency": r["consistency"], "score": r["score"],
        "support_k": r["support_k"], "support_n": r["support_n"],
        "automated": bool(r["automated"]), "automation_ref": r["automation_ref"],
        "evidence": r["evidence"], "narration": r["narration"], "mined_at": r["mined_at"],
    }


def query(kind: str | None = None, automated: bool | None = None,
          min_consistency: float | None = None, entity: str | None = None,
          limit: int = 200) -> list[dict]:
    init()
    where, args = [], []
    if kind:
        where.append("kind = ?"); args.append(kind)
    if automated is not None:
        where.append("automated = ?"); args.append(1 if automated else 0)
    if min_consistency is not None:
        where.append("consistency >= ?"); args.append(min_consistency)
    if entity:
        where.append("(entity_id = ? OR peer_id = ?)"); args += [entity, entity]
    sql = ("SELECT * FROM patterns" + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY score DESC, consistency DESC LIMIT ?")
    args.append(limit)
    with _conn() as c:
        rows = c.execute(sql, args).fetchall()
    return [_row(r) for r in rows]


def last_run() -> dict | None:
    init()
    with _conn() as c:
        r = c.execute("SELECT * FROM runs ORDER BY mined_at DESC, id DESC LIMIT 1").fetchone()
        counts = {row["kind"]: row["n"] for row in
                  c.execute("SELECT kind, COUNT(*) n FROM patterns GROUP BY kind")}
    if not r:
        return None
    return {
        "mined_at": r["mined_at"], "window_start": r["window_start"], "window_end": r["window_end"],
        "events_scanned": r["events_scanned"], "n_patterns": r["n_patterns"],
        "n_automated": r["n_automated"], "n_candidate": r["n_candidate"], "by_kind": counts,
    }
