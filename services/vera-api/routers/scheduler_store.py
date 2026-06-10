"""Scheduler store — per-job schedule/enabled overrides + last outcome, in SQLite.

The job REGISTRY (what can run, with what default cron) lives in scheduler.py; this
store holds only the deltas a deployment makes at runtime (Agentic-tab edits) plus
each job's last outcome. Env overrides (SCHEDULE_<JOB>, SCHEDULE_<JOB>_ENABLED) win
over rows here at boot — env is for headless installs, the db is for live edits.
"""

import os
import sqlite3
import time

DB_PATH = os.environ.get("SCHEDULER_DB_PATH", "/data/scheduler.db")


def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        cron TEXT,
        enabled INTEGER,
        last_ts REAL,
        last_ok INTEGER,
        last_detail TEXT
    )""")
    return c


def overrides() -> dict[str, dict]:
    """All persisted rows, keyed by job id. Missing fields stay None (registry default applies)."""
    with _conn() as c:
        return {r["id"]: dict(r) for r in c.execute("SELECT * FROM jobs")}


def set_override(job_id: str, cron: str | None = None, enabled: bool | None = None) -> None:
    with _conn() as c:
        c.execute("INSERT INTO jobs (id) VALUES (?) ON CONFLICT(id) DO NOTHING", (job_id,))
        if cron is not None:
            c.execute("UPDATE jobs SET cron = ? WHERE id = ?", (cron, job_id))
        if enabled is not None:
            c.execute("UPDATE jobs SET enabled = ? WHERE id = ?", (1 if enabled else 0, job_id))


def record_outcome(job_id: str, ok: bool, detail: str) -> None:
    with _conn() as c:
        c.execute("INSERT INTO jobs (id) VALUES (?) ON CONFLICT(id) DO NOTHING", (job_id,))
        c.execute("UPDATE jobs SET last_ts = ?, last_ok = ?, last_detail = ? WHERE id = ?",
                  (time.time(), 1 if ok else 0, detail[:500], job_id))
