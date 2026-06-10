"""Action store — staged-then-committed actions with an audit log.

SQLite at /data/actions.db. Generalizes the knowledge store's pending+token+idempotent-commit pattern:
`stage()` returns a content-hash token (identical proposals dedupe), `set_result()` applies it once
and writes an audit row. Pure sqlite — the typed registry + executors live in actions.py.
"""
import hashlib
import json
import os
import sqlite3
import time

DB_PATH = os.environ.get("ACTION_DB_PATH", "/data/actions.db")


def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS action_pending (
                token TEXT PRIMARY KEY, created_at INTEGER, verb TEXT, args TEXT,
                preview TEXT, risk TEXT, reversible INTEGER,
                source TEXT, actor TEXT, chat_id TEXT, message_id TEXT,
                status TEXT, result TEXT
            );
            CREATE TABLE IF NOT EXISTS action_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, token TEXT,
                verb TEXT, args TEXT, result TEXT, status TEXT
            );
            """
        )


def stage(verb, args, preview, risk, reversible,
          source="chat", actor="vera", chat_id=None, message_id=None):
    """Stage an action. Token is a content hash of (verb, args) — identical proposals dedupe."""
    init()
    token = hashlib.sha1(json.dumps({"verb": verb, "args": args}, sort_keys=True).encode()).hexdigest()[:8]
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO action_pending VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (token, int(time.time()), verb, json.dumps(args), preview, risk, 1 if reversible else 0,
             source, actor, chat_id, message_id, "pending", None),
        )
    return token


def _row(r):
    return {
        "token": r["token"], "created_at": r["created_at"], "verb": r["verb"],
        "args": json.loads(r["args"] or "{}"), "preview": r["preview"], "risk": r["risk"],
        "reversible": bool(r["reversible"]), "source": r["source"], "actor": r["actor"],
        "chat_id": r["chat_id"], "message_id": r["message_id"], "status": r["status"],
        "result": json.loads(r["result"]) if r["result"] else None,
    }


def get(token):
    init()
    with _conn() as c:
        r = c.execute("SELECT * FROM action_pending WHERE token=?", (token,)).fetchone()
    return _row(r) if r else None


def set_result(token, result: dict, status="applied"):
    """Record an action's outcome and append an audit-log row."""
    init()
    with _conn() as c:
        row = c.execute("SELECT verb, args FROM action_pending WHERE token=?", (token,)).fetchone()
        c.execute("UPDATE action_pending SET status=?, result=? WHERE token=?",
                  (status, json.dumps(result), token))
        c.execute(
            "INSERT INTO action_log(ts, token, verb, args, result, status) VALUES(?,?,?,?,?,?)",
            (int(time.time()), token, row["verb"] if row else None,
             row["args"] if row else "{}", json.dumps(result), status),
        )


def dismiss(token):
    init()
    with _conn() as c:
        c.execute("UPDATE action_pending SET status='dismissed' WHERE token=?", (token,))


def recent_log(limit=50):
    init()
    with _conn() as c:
        rows = c.execute(
            "SELECT ts, token, verb, args, result, status FROM action_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {"ts": r["ts"], "token": r["token"], "verb": r["verb"],
         "args": json.loads(r["args"] or "{}"),
         "result": json.loads(r["result"]) if r["result"] else None, "status": r["status"]}
        for r in rows
    ]
