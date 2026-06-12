"""Scheduler store — per-job schedule/enabled overrides + run outcomes, in SQLite.

The job REGISTRY (what can run, with what default cron) lives in scheduler.py; this
store holds only the deltas a deployment makes at runtime (Agentic-tab edits) plus
run outcomes: a last-run snapshot per job (drives the schedule rows in the app) and
an append-only run log (feeds /agentic/activity). Env overrides (SCHEDULE_<JOB>,
SCHEDULE_<JOB>_ENABLED) win over rows here at boot — env is for headless installs,
the db is for live edits.
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
    c.execute("""CREATE TABLE IF NOT EXISTS run_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL,
        job_id TEXT,
        ok INTEGER,
        detail TEXT
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_run_log_ts ON run_log(ts)")
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
    now = time.time()
    with _conn() as c:
        c.execute("INSERT INTO jobs (id) VALUES (?) ON CONFLICT(id) DO NOTHING", (job_id,))
        c.execute("UPDATE jobs SET last_ts = ?, last_ok = ?, last_detail = ? WHERE id = ?",
                  (now, 1 if ok else 0, detail[:500], job_id))
        c.execute("INSERT INTO run_log (ts, job_id, ok, detail) VALUES (?, ?, ?, ?)",
                  (now, job_id, 1 if ok else 0, detail[:500]))


def recent_runs(hours: int = 24) -> list[dict]:
    """Run-log rows within the window, newest first."""
    cutoff = time.time() - hours * 3600
    with _conn() as c:
        rows = c.execute("SELECT ts, job_id, ok, detail FROM run_log WHERE ts > ? ORDER BY id DESC",
                         (cutoff,)).fetchall()
    return [{"ts": r["ts"], "job_id": r["job_id"], "ok": bool(r["ok"]), "detail": r["detail"]}
            for r in rows]
