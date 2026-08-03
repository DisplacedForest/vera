import json
import os
import sqlite3
import time
import uuid

import data_root


DB_PATH = os.environ.get("WORKFLOW_DB_PATH", os.path.join(data_root.resolve(), "workflows.db"))
SUPPORTED_NODE_TYPES = {
    "pulse.triage", "pulse.gates", "pulse.synthesis", "pulse.claim_audit",
    "pulse.cover_art", "pulse.visual_review", "pulse.cover_retry", "pulse.inject",
}
REQUIRED_NODE_TYPES = {
    "pulse.triage", "pulse.gates", "pulse.synthesis", "pulse.claim_audit",
    "pulse.cover_art", "pulse.inject",
}


def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _pulse_definition() -> dict:
    return {
        "id": "pulse",
        "label": "Pulse briefing",
        "layout": "pipeline",
        "nodes": [
            {"id": "triage", "type": "pulse.triage", "label": "Triage", "icon": "globe", "tint": "accent"},
            {"id": "gates", "type": "pulse.gates", "label": "Gates", "icon": "line.3.horizontal.decrease.circle", "tint": "orange"},
            {"id": "synthesis", "type": "pulse.synthesis", "label": "Synthesis", "icon": "sparkles", "tint": "purple"},
            {"id": "claim_audit", "type": "pulse.claim_audit", "label": "Claim audit", "icon": "checkmark.shield", "tint": "cyan"},
            {"id": "cover_art", "type": "pulse.cover_art", "label": "Cover art", "icon": "photo", "tint": "purple", "config": {"style": "rotating"}},
            {"id": "inject", "type": "pulse.inject", "label": "Inject", "icon": "arrow.down.to.line", "tint": "green"},
        ],
        "edges": [
            {"from": "triage", "to": "gates"},
            {"from": "gates", "to": "synthesis"},
            {"from": "synthesis", "to": "claim_audit"},
            {"from": "claim_audit", "to": "cover_art"},
            {"from": "cover_art", "to": "inject"},
        ],
    }


def init():
    with _conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS workflow_versions (
            id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, version INTEGER NOT NULL,
            state TEXT NOT NULL, definition TEXT NOT NULL, created_at INTEGER NOT NULL,
            promoted_at INTEGER, UNIQUE(workflow_id, version))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS workflow_runs (
            id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, workflow_version_id TEXT NOT NULL,
            state TEXT NOT NULL, started_at INTEGER NOT NULL, finished_at INTEGER, output TEXT, error TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS workflow_node_runs (
            id TEXT PRIMARY KEY, workflow_run_id TEXT NOT NULL, node_id TEXT NOT NULL,
            state TEXT NOT NULL, output TEXT, error TEXT, started_at INTEGER,
            finished_at INTEGER, created_at INTEGER NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS workflow_visual_runs (
            id TEXT PRIMARY KEY, workflow_run_id TEXT NOT NULL, card_id TEXT NOT NULL,
            image_url TEXT, review TEXT, retry_count INTEGER NOT NULL, state TEXT NOT NULL,
            created_at INTEGER NOT NULL)""")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(workflow_node_runs)")}
        for name, kind in (("error", "TEXT"), ("started_at", "INTEGER"), ("finished_at", "INTEGER")):
            if name not in columns:
                conn.execute(f"ALTER TABLE workflow_node_runs ADD COLUMN {name} {kind}")
        row = conn.execute("SELECT id FROM workflow_versions WHERE workflow_id='pulse' AND state='active'").fetchone()
        if not row:
            now = int(time.time())
            conn.execute("INSERT INTO workflow_versions(id, workflow_id, version, state, definition, created_at, promoted_at) VALUES(?,?,?,?,?,?,?)",
                         (str(uuid.uuid4()), "pulse", 1, "active", json.dumps(_pulse_definition()), now, now))


def _version_row(workflow_id: str, state: str = "active"):
    init()
    with _conn() as conn:
        return conn.execute("SELECT * FROM workflow_versions WHERE workflow_id=? AND state=? ORDER BY version DESC LIMIT 1", (workflow_id, state)).fetchone()


def _version(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "workflow_id": row["workflow_id"], "version": row["version"], "state": row["state"],
            "definition": json.loads(row["definition"]), "created_at": row["created_at"], "promoted_at": row["promoted_at"]}


def active(workflow_id: str) -> dict:
    row = _version_row(workflow_id)
    if not row:
        raise KeyError(workflow_id)
    return _version(row)


def create_draft(workflow_id: str) -> dict:
    current = active(workflow_id)
    now = int(time.time())
    with _conn() as conn:
        number = conn.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM workflow_versions WHERE workflow_id=?", (workflow_id,)).fetchone()[0]
        ident = str(uuid.uuid4())
        conn.execute("INSERT INTO workflow_versions(id, workflow_id, version, state, definition, created_at) VALUES(?,?,?,?,?,?)",
                     (ident, workflow_id, number, "draft", json.dumps(current["definition"]), now))
    return get_version(ident)


def get_version(version_id: str) -> dict | None:
    init()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM workflow_versions WHERE id=?", (version_id,)).fetchone()
    return _version(row) if row else None


def save_draft(version_id: str, definition: dict) -> dict:
    current = get_version(version_id)
    if not current or current["state"] != "draft":
        raise ValueError("draft not found")
    if definition.get("id") != current["workflow_id"]:
        raise ValueError("workflow id cannot change")
    nodes = definition.get("nodes")
    edges = definition.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list) or not nodes:
        raise ValueError("nodes and edges are required")
    ids = [node.get("id") for node in nodes]
    if any(not isinstance(node_id, str) or not node_id for node_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("nodes need unique ids")
    if any(node.get("type") not in SUPPORTED_NODE_TYPES for node in nodes):
        raise ValueError("workflow uses an unsupported node type")
    types = [node.get("type") for node in nodes]
    if not REQUIRED_NODE_TYPES.issubset(types) or len(types) != len(set(types)):
        raise ValueError("workflow must contain each required node exactly once")
    has_review = "pulse.visual_review" in types
    has_retry = "pulse.cover_retry" in types
    if has_review != has_retry:
        raise ValueError("visual review and retry must be added together")
    if any(edge.get("from") not in ids or edge.get("to") not in ids for edge in edges):
        raise ValueError("edges must connect declared nodes")
    id_for = {node["type"]: node["id"] for node in nodes}
    chain = ["pulse.triage", "pulse.gates", "pulse.synthesis", "pulse.claim_audit", "pulse.cover_art"]
    if has_review:
        chain.extend(["pulse.visual_review", "pulse.cover_retry"])
    chain.append("pulse.inject")
    expected_edges = [{"from": id_for[left], "to": id_for[right]} for left, right in zip(chain, chain[1:])]
    if edges != expected_edges:
        raise ValueError("workflow must preserve the approved Pulse execution order")
    for node in nodes:
        config = node.get("config") or {}
        if not isinstance(config, dict):
            raise ValueError("node configuration must be an object")
        if node["type"] == "pulse.cover_art":
            if set(config) - {"style"} or config.get("style", "rotating") not in {"rotating", "photographic", "illustrated", "editorial"}:
                raise ValueError("cover art style must be rotating, photographic, illustrated, or editorial")
        if node["type"] == "pulse.visual_review":
            threshold = config.get("threshold", 0.8)
            if set(config) - {"threshold"} or not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
                raise ValueError("visual review threshold must be between 0 and 1")
        if node["type"] == "pulse.cover_retry":
            attempts = config.get("max_attempts", 1)
            if set(config) - {"max_attempts"} or attempts not in (0, 1):
                raise ValueError("cover retry allows zero or one attempt")
    with _conn() as conn:
        conn.execute("UPDATE workflow_versions SET definition=? WHERE id=?", (json.dumps(definition), version_id))
    return get_version(version_id)


def promote(version_id: str) -> dict:
    draft = get_version(version_id)
    if not draft or draft["state"] != "draft":
        raise ValueError("draft not found")
    now = int(time.time())
    with _conn() as conn:
        conn.execute("UPDATE workflow_versions SET state='archived' WHERE workflow_id=? AND state='active'", (draft["workflow_id"],))
        conn.execute("UPDATE workflow_versions SET state='active', promoted_at=? WHERE id=?", (now, version_id))
    return get_version(version_id)


def start_run(workflow_id: str, run_id: str) -> dict:
    version = active(workflow_id)
    now = int(time.time())
    with _conn() as conn:
        conn.execute("INSERT OR REPLACE INTO workflow_runs(id, workflow_id, workflow_version_id, state, started_at) VALUES(?,?,?,?,?)",
                     (run_id, workflow_id, version["id"], "running", now))
    return version


def finish_run(run_id: str, state: str, output: dict | None = None, error: str | None = None):
    with _conn() as conn:
        conn.execute("UPDATE workflow_runs SET state=?, finished_at=?, output=?, error=? WHERE id=?",
                     (state, int(time.time()), json.dumps(output or {}), error, run_id))


def start_node(run_id: str, node_id: str) -> str:
    ident = str(uuid.uuid4())
    now = int(time.time())
    with _conn() as conn:
        conn.execute("INSERT INTO workflow_node_runs(id, workflow_run_id, node_id, state, started_at, created_at) VALUES(?,?,?,?,?,?)",
                     (ident, run_id, node_id, "running", now, now))
    return ident


def finish_node(node_run_id: str, state: str, output: dict | None = None, error: str | None = None):
    with _conn() as conn:
        conn.execute("UPDATE workflow_node_runs SET state=?, output=?, error=?, finished_at=? WHERE id=?",
                     (state, json.dumps(output or {}), error, int(time.time()), node_run_id))


def record_visual_run(run_id: str, card_id: str, image_url: str | None, review: dict | None, retry_count: int, state: str):
    with _conn() as conn:
        conn.execute("INSERT INTO workflow_visual_runs(id, workflow_run_id, card_id, image_url, review, retry_count, state, created_at) VALUES(?,?,?,?,?,?,?,?)",
                     (str(uuid.uuid4()), run_id, card_id, image_url, json.dumps(review or {}), retry_count, state, int(time.time())))


def record_node_runs(run_id: str, output: dict):
    status = "ok" if not output.get("errors") else "warning"
    gates = output.get("gates") or {}
    outputs = {
        "triage": {"rounds": len(output.get("rounds") or []), "proposed": len(output.get("topics") or [])},
        "gates": {"gates": gates},
        "synthesis": {"cards": len(output.get("injected") or [])},
        "claim_audit": {"items": len(output.get("items") or [])},
        "cover_art": {"generated": sum(1 for item in output.get("items") or [] if item.get("cover_generated"))},
        "visual_review": {"reviewed": sum(1 for item in output.get("items") or [] if item.get("visual_review"))},
        "cover_retry": {"attempted": sum(1 for item in output.get("items") or [] if item.get("visual_retry"))},
        "inject": {"cards": len(output.get("injected") or [])},
    }
    with _conn() as conn:
        row = conn.execute("""SELECT v.definition FROM workflow_runs r
                              JOIN workflow_versions v ON v.id=r.workflow_version_id
                              WHERE r.id=?""", (run_id,)).fetchone()
    if not row:
        raise ValueError("workflow run not found")
    nodes = json.loads(row["definition"])["nodes"]
    rows = [
        ("triage", {"rounds": len(output.get("rounds") or []), "proposed": len(output.get("topics") or [])}),
        *[(node["id"], outputs.get(node["id"], {})) for node in nodes if node["id"] != "triage"],
    ]
    now = int(time.time())
    with _conn() as conn:
        existing = {row[0] for row in conn.execute("SELECT DISTINCT node_id FROM workflow_node_runs WHERE workflow_run_id=?", (run_id,))}
        conn.executemany("INSERT INTO workflow_node_runs(id, workflow_run_id, node_id, state, output, created_at) VALUES(?,?,?,?,?,?)",
                         [(str(uuid.uuid4()), run_id, node_id, status, json.dumps(node_output), now) for node_id, node_output in rows if node_id not in existing])


def latest_run(workflow_id: str) -> dict | None:
    init()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM workflow_runs WHERE workflow_id=? ORDER BY started_at DESC LIMIT 1", (workflow_id,)).fetchone()
        if not row:
            return None
        nodes = conn.execute("SELECT node_id, state, output, error, started_at, finished_at, created_at FROM workflow_node_runs WHERE workflow_run_id=? ORDER BY created_at", (row["id"],)).fetchall()
        visual = conn.execute("SELECT card_id, image_url, review, retry_count, state, created_at FROM workflow_visual_runs WHERE workflow_run_id=? ORDER BY created_at", (row["id"],)).fetchall()
    return {"id": row["id"], "workflow_id": row["workflow_id"], "workflow_version_id": row["workflow_version_id"],
            "state": row["state"], "started_at": row["started_at"], "finished_at": row["finished_at"],
            "output": json.loads(row["output"] or "{}"), "error": row["error"],
            "nodes": [{"id": node["node_id"], "state": node["state"], "output": json.loads(node["output"] or "{}"), "error": node["error"], "started_at": node["started_at"], "finished_at": node["finished_at"], "created_at": node["created_at"]} for node in nodes],
            "visual_runs": [{"card_id": item["card_id"], "image_url": item["image_url"], "review": json.loads(item["review"] or "{}"), "retry_count": item["retry_count"], "state": item["state"], "created_at": item["created_at"]} for item in visual]}
