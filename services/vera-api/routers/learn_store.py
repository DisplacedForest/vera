"""Learn store — the feedback loop's persistence.

Three tables: `card_link` (which graph nodes a card served + the five ranking features it
scored), `outcome` (every reaction signal on a card), and `weights` (the single learned-
coefficient row the fit installs). The links join a card's features to its eventual outcome,
producing the labeled set the logistic-regression fit reads. Follows the `*_store.py` shape:
an env-bound SQLite path, `_conn()`, and an idempotent `init()`.
"""
import json
import os
import sqlite3

DB_PATH = os.environ.get("LEARN_DB_PATH", "/data/learn/store.db")

FEATURE_KEYS = ["relevance", "novelty", "opportunity", "urgency", "serendipity"]

POSITIVE = {"up", "open", "opened", "bookmark", "bookmarked", "promote", "promoted", "discussed"}
NEGATIVE = {"down", "expire", "expired", "ignore", "ignored"}


def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS card_link (
                card_id TEXT PRIMARY KEY,
                nodes TEXT,        -- json [node_id]
                features TEXT,     -- json {feature: value}
                ts INTEGER
            );
            CREATE TABLE IF NOT EXISTS outcome (
                card_id TEXT,
                signal TEXT,
                ts INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_outcome_card ON outcome(card_id);
            CREATE TABLE IF NOT EXISTS weights (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                coeffs TEXT,
                n_samples INTEGER,
                ts INTEGER
            );
            """
        )


def record_card(card_id, nodes, features, now):
    """Link a shipped card to the nodes it served and the features it scored. Idempotent by
    card_id (a re-ship overwrites)."""
    init()
    with _conn() as c:
        c.execute(
            """INSERT INTO card_link(card_id,nodes,features,ts) VALUES(?,?,?,?)
               ON CONFLICT(card_id) DO UPDATE SET nodes=excluded.nodes,
                 features=excluded.features, ts=excluded.ts""",
            (card_id, json.dumps(list(nodes or [])), json.dumps(features or {}), int(now)))


def record_outcome(card_id, signal, now):
    init()
    with _conn() as c:
        c.execute("INSERT INTO outcome(card_id,signal,ts) VALUES(?,?,?)",
                  (card_id, signal, int(now)))


def link(card_id):
    init()
    with _conn() as c:
        r = c.execute("SELECT nodes,features FROM card_link WHERE card_id=?", (card_id,)).fetchone()
    if not r:
        return None
    return {"nodes": json.loads(r["nodes"] or "[]"), "features": json.loads(r["features"] or "{}")}


def served_nodes():
    """Every node id that has served a card — the set whose re-observation in a later
    conversation counts as 'discussed later'."""
    init()
    with _conn() as c:
        rows = c.execute("SELECT nodes FROM card_link").fetchall()
    out = set()
    for r in rows:
        out.update(json.loads(r["nodes"] or "[]"))
    return out


def _label(signal):
    """A signal to a binary outcome label, or None when the signal carries no preference."""
    if signal in POSITIVE:
        return 1
    if signal in NEGATIVE:
        return 0
    return None


def labeled_examples():
    """One `(features, label)` per card that has an outcome — the card's latest outcome decides
    the label. Features are ordered by FEATURE_KEYS. Cards with no outcome (or only neutral
    signals) are excluded."""
    init()
    with _conn() as c:
        rows = c.execute(
            """SELECT cl.features AS features, o.signal AS signal FROM card_link cl
               JOIN outcome o ON o.card_id = cl.card_id
               JOIN (SELECT card_id, MAX(ts) AS mts FROM outcome GROUP BY card_id) last
                 ON last.card_id = o.card_id AND last.mts = o.ts
               GROUP BY cl.card_id""").fetchall()
    out = []
    for r in rows:
        y = _label(r["signal"])
        if y is None:
            continue
        feat = json.loads(r["features"] or "{}")
        out.append(({k: float(feat.get(k, 0.0)) for k in FEATURE_KEYS}, y))
    return out


def get_weights():
    init()
    with _conn() as c:
        r = c.execute("SELECT coeffs,n_samples,ts FROM weights WHERE id=1").fetchone()
    if not r:
        return None
    return {"coeffs": json.loads(r["coeffs"] or "{}"), "n_samples": r["n_samples"], "ts": r["ts"]}


def set_weights(coeffs, n_samples, now):
    init()
    with _conn() as c:
        c.execute(
            """INSERT INTO weights(id,coeffs,n_samples,ts) VALUES(1,?,?,?)
               ON CONFLICT(id) DO UPDATE SET coeffs=excluded.coeffs,
                 n_samples=excluded.n_samples, ts=excluded.ts""",
            (json.dumps(coeffs), int(n_samples), int(now)))
