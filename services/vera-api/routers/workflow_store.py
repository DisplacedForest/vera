import json
import os
import sqlite3
import time
import uuid

import data_root

from . import workflow_registry
from . import workflow_triggers


DB_PATH = os.environ.get("WORKFLOW_DB_PATH", os.path.join(data_root.resolve(), "workflows.db"))

TRIGGER_PLACEMENT_PITCH = 184
TRIGGER_MIN_X = 60


def _job_cron(workflow_id: str) -> str | None:
    job_id = workflow_triggers.JOB_FOR_WORKFLOW.get(workflow_id)
    if not job_id:
        return None
    try:
        from . import scheduler
        return scheduler.job_cron(job_id)
    except Exception:
        return None


def _trigger_node_id(definition: dict) -> str | None:
    for node in definition.get("nodes") or []:
        if workflow_triggers.is_trigger(node.get("type")):
            return node["id"]
    return None


def _with_trigger(definition: dict, workflow_id: str, cron: str | None) -> tuple[dict, bool]:
    if workflow_id not in workflow_triggers.JOB_FOR_WORKFLOW or _trigger_node_id(definition):
        return definition, False
    nodes = list(definition.get("nodes") or [])
    edges = list(definition.get("edges") or [])
    if not nodes:
        return definition, False
    targets = {edge["to"] for edge in edges}
    head = next((node["id"] for node in nodes if node["id"] not in targets), nodes[0]["id"])
    node_id = "schedule"
    while any(node["id"] == node_id for node in nodes):
        node_id = f"{node_id}-trigger"
    config = workflow_triggers.config_for(cron) or dict(workflow_triggers.DEFAULT_CONFIG)
    trigger = {"id": node_id, "type": workflow_triggers.SCHEDULE_TYPE, "config": config}
    migrated = {**definition, "nodes": [trigger, *nodes],
                "edges": [{"from": node_id, "to": head}, *edges]}
    positions = definition.get("positions")
    if isinstance(positions, dict) and isinstance(positions.get(head), dict):
        head_pos = positions[head]
        x = head_pos.get("x", 0) - TRIGGER_PLACEMENT_PITCH
        shifted = dict(positions)
        if x < TRIGGER_MIN_X:
            delta = TRIGGER_MIN_X - x
            shifted = {key: {**value, "x": value.get("x", 0) + delta}
                       for key, value in positions.items() if isinstance(value, dict)}
            x = TRIGGER_MIN_X
        shifted[node_id] = {"x": x, "y": head_pos.get("y", 0)}
        migrated["positions"] = shifted
    return migrated, True


def _sync_trigger_config(definition: dict, workflow_id: str) -> dict:
    node_id = _trigger_node_id(definition)
    if not node_id or workflow_id not in workflow_triggers.JOB_FOR_WORKFLOW:
        return definition
    live = workflow_triggers.config_for(_job_cron(workflow_id))
    if live is None:
        return definition
    nodes = [({**node, "config": live} if node["id"] == node_id else node)
             for node in definition.get("nodes") or []]
    return {**definition, "nodes": nodes}


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
            {"id": "schedule", "type": "trigger.schedule", "config": {"mode": "daily", "time": "05:00"}},
            {"id": "triage", "type": "pulse.triage", "label": "Triage", "icon": "globe", "tint": "accent"},
            {"id": "gates", "type": "pulse.gates", "label": "Gates", "icon": "line.3.horizontal.decrease.circle", "tint": "orange"},
            {"id": "synthesis", "type": "pulse.synthesis", "label": "Synthesis", "icon": "sparkles", "tint": "purple"},
            {"id": "claim_audit", "type": "pulse.claim_audit", "label": "Claim audit", "icon": "checkmark.shield", "tint": "cyan"},
            {"id": "cover_art", "type": "pulse.cover_art", "label": "Cover art", "icon": "photo", "tint": "purple", "config": {"style": "rotating"}},
            {"id": "inject", "type": "pulse.inject", "label": "Inject", "icon": "arrow.down.to.line", "tint": "green"},
        ],
        "edges": [
            {"from": "schedule", "to": "triage"},
            {"from": "triage", "to": "gates"},
            {"from": "gates", "to": "synthesis"},
            {"from": "synthesis", "to": "claim_audit"},
            {"from": "claim_audit", "to": "cover_art"},
            {"from": "cover_art", "to": "inject"},
        ],
    }


def baseline_definition() -> dict:
    return _pulse_definition()


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
            state TEXT NOT NULL, input TEXT, output TEXT, error TEXT, started_at INTEGER,
            finished_at INTEGER, created_at INTEGER NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS workflow_visual_runs (
            id TEXT PRIMARY KEY, workflow_run_id TEXT NOT NULL, card_id TEXT NOT NULL,
            image_url TEXT, review TEXT, retry_count INTEGER NOT NULL, state TEXT NOT NULL,
            created_at INTEGER NOT NULL)""")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(workflow_node_runs)")}
        for name, kind in (("error", "TEXT"), ("started_at", "INTEGER"), ("finished_at", "INTEGER"), ("input", "TEXT")):
            if name not in columns:
                conn.execute(f"ALTER TABLE workflow_node_runs ADD COLUMN {name} {kind}")
        row = conn.execute("SELECT id FROM workflow_versions WHERE workflow_id='pulse' AND state='active'").fetchone()
        if not row:
            now = int(time.time())
            conn.execute("INSERT INTO workflow_versions(id, workflow_id, version, state, definition, created_at, promoted_at) VALUES(?,?,?,?,?,?,?)",
                         (str(uuid.uuid4()), "pulse", 1, "active", json.dumps(_pulse_definition()), now, now))
        conn.execute("CREATE TABLE IF NOT EXISTS workflow_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        done = conn.execute("SELECT value FROM workflow_meta WHERE key='trigger_migration'").fetchone()
        if not done:
            crons: dict[str, str | None] = {}
            complete = True
            for version_row in conn.execute("SELECT id, workflow_id, definition FROM workflow_versions WHERE state IN ('active','draft')").fetchall():
                workflow_id = version_row["workflow_id"]
                if workflow_id not in workflow_triggers.JOB_FOR_WORKFLOW:
                    continue
                if workflow_id not in crons:
                    crons[workflow_id] = _job_cron(workflow_id)
                if crons[workflow_id] is None:
                    complete = False
                    continue
                definition = json.loads(version_row["definition"])
                migrated, changed = _with_trigger(definition, workflow_id, crons[workflow_id])
                if changed:
                    conn.execute("UPDATE workflow_versions SET definition=? WHERE id=?",
                                 (json.dumps(migrated), version_row["id"]))
            if complete:
                conn.execute("INSERT OR REPLACE INTO workflow_meta(key, value) VALUES('trigger_migration','1')")


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
    version = _version(row)
    version["definition"] = _sync_trigger_config(version["definition"], workflow_id)
    return version


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
    workflow_registry.validate_definition(current["workflow_id"], definition)
    with _conn() as conn:
        conn.execute("UPDATE workflow_versions SET definition=? WHERE id=?", (json.dumps(definition), version_id))
    return get_version(version_id)


def promote(version_id: str) -> dict:
    draft = get_version(version_id)
    if not draft or draft["state"] != "draft":
        raise ValueError("draft not found")
    workflow_registry.validate_promotion(draft["workflow_id"], draft["definition"])
    plan = _trigger_schedule_plan(draft["workflow_id"], draft["definition"])
    now = int(time.time())
    with _conn() as conn:
        previous = conn.execute("SELECT id FROM workflow_versions WHERE workflow_id=? AND state='active'",
                                (draft["workflow_id"],)).fetchone()
        conn.execute("UPDATE workflow_versions SET state='archived' WHERE workflow_id=? AND state='active'", (draft["workflow_id"],))
        conn.execute("UPDATE workflow_versions SET state='active', promoted_at=? WHERE id=?", (now, version_id))
    if plan:
        try:
            from . import scheduler
            scheduler.set_job_cron(*plan)
        except Exception:
            with _conn() as conn:
                conn.execute("UPDATE workflow_versions SET state='draft', promoted_at=NULL WHERE id=?", (version_id,))
                if previous:
                    conn.execute("UPDATE workflow_versions SET state='active' WHERE id=?", (previous["id"],))
            raise ValueError("the schedule could not be applied, so the promotion was rolled back")
    return get_version(version_id)


def _trigger_config(definition: dict) -> dict | None:
    node_id = _trigger_node_id(definition)
    if not node_id:
        return None
    node = next(node for node in definition["nodes"] if node["id"] == node_id)
    return node.get("config") or {}


def _trigger_schedule_plan(workflow_id: str, definition: dict) -> tuple[str, str] | None:
    job_id = workflow_triggers.JOB_FOR_WORKFLOW.get(workflow_id)
    config = _trigger_config(definition)
    if not job_id or config is None:
        return None
    current = None
    row = _version_row(workflow_id)
    if row:
        current = _trigger_config(_version(row)["definition"])
    if config == current:
        return None
    cron = workflow_triggers.cron_for(config)
    from . import scheduler
    scheduler.ensure_job_cron_allowed(job_id, cron)
    if cron == scheduler.job_cron(job_id):
        return None
    return (job_id, cron)


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


def start_node(run_id: str, node_id: str, input: dict | None = None) -> str:
    ident = str(uuid.uuid4())
    now = int(time.time())
    with _conn() as conn:
        conn.execute("INSERT INTO workflow_node_runs(id, workflow_run_id, node_id, state, input, started_at, created_at) VALUES(?,?,?,?,?,?,?)",
                     (ident, run_id, node_id, "running", json.dumps(input) if input is not None else None, now, now))
    return ident


def finish_node(node_run_id: str, state: str, output: dict | None = None, error: str | None = None,
                input: dict | None = None):
    with _conn() as conn:
        conn.execute("UPDATE workflow_node_runs SET state=?, output=?, error=?, finished_at=?, input=COALESCE(?, input) WHERE id=?",
                     (state, json.dumps(output or {}), error, int(time.time()),
                      json.dumps(input) if input is not None else None, node_run_id))


def record_visual_run(run_id: str, card_id: str, image_url: str | None, review: dict | None, retry_count: int, state: str):
    with _conn() as conn:
        conn.execute("INSERT INTO workflow_visual_runs(id, workflow_run_id, card_id, image_url, review, retry_count, state, created_at) VALUES(?,?,?,?,?,?,?,?)",
                     (str(uuid.uuid4()), run_id, card_id, image_url, json.dumps(review or {}), retry_count, state, int(time.time())))


def latest_run(workflow_id: str) -> dict | None:
    init()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM workflow_runs WHERE workflow_id=? ORDER BY started_at DESC LIMIT 1", (workflow_id,)).fetchone()
        if not row:
            return None
        nodes = conn.execute("SELECT node_id, state, input, output, error, started_at, finished_at, created_at FROM workflow_node_runs WHERE workflow_run_id=? ORDER BY created_at", (row["id"],)).fetchall()
        visual = conn.execute("SELECT card_id, image_url, review, retry_count, state, created_at FROM workflow_visual_runs WHERE workflow_run_id=? ORDER BY created_at", (row["id"],)).fetchall()
    return {"id": row["id"], "workflow_id": row["workflow_id"], "workflow_version_id": row["workflow_version_id"],
            "version": get_version(row["workflow_version_id"]),
            "state": row["state"], "started_at": row["started_at"], "finished_at": row["finished_at"],
            "output": json.loads(row["output"] or "{}"), "error": row["error"],
            "nodes": [{"id": node["node_id"], "state": node["state"], "input": json.loads(node["input"]) if node["input"] else None, "output": json.loads(node["output"] or "{}"), "error": node["error"], "started_at": node["started_at"], "finished_at": node["finished_at"], "created_at": node["created_at"]} for node in nodes],
            "visual_runs": [{"card_id": item["card_id"], "image_url": item["image_url"], "review": json.loads(item["review"] or "{}"), "retry_count": item["retry_count"], "state": item["state"], "created_at": item["created_at"]} for item in visual]}
