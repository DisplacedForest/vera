"""Pytest bootstrap: point every store at a writable temp dir BEFORE
any module is imported.

Each store binds its DB path at import time from a `*_DB_PATH` / `*_DIR` env var
that defaults to `/data/...` (the container's bind mount). On a host without a
writable `/data`, a store imported transitively during collection (e.g.
`routers.home` -> `routers.rhythm_store`) binds to `/data` before a test's own
line-6 override runs, and `init()` fails with a read-only-filesystem error.

pytest imports conftest.py before collecting test modules, so setting these env
vars here puts a writable default in place before any store binds its path —
making the suite host-independent without touching store logic. `setdefault`
keeps any value already provided by the environment (e.g. CI) authoritative.
"""
import os
import tempfile

_ROOT = tempfile.mkdtemp(prefix="vera-api-tests-")

# env var -> path relative to the temp root (mirrors each store's /data default)
_STORE_PATHS = {
    "ACTION_DB_PATH": "actions.db",
    "AUTHORING_DB_PATH": "authoring.db",
    "FEEDBACK_PATH": "feedback.jsonl",
    "HEARTBEAT_DB_PATH": "heartbeat.db",
    "HOME_EVENTS_DB_PATH": "home_events.db",
    "HOME_MODEL_DB_PATH": "home_model.db",
    "HOME_RECONCILE_DB_PATH": "home_reconcile.db",
    "GROOM_DB_PATH": "groom.db",
    "KNOWLEDGE_DB_PATH": "knowledge.db",
    "VEINS_PATH": "veins.json",
    "MEDIA_DB_PATH": "media.db",
    "PULSE_DB_PATH": "pulse.db",
    "SIGNALS_LOG_PATH": "signals_log.jsonl",
    "ANALYST_LOG_PATH": "analyst_log.jsonl",
    "RHYTHM_DB_PATH": "rhythm.db",
    "SCHEDULER_DB_PATH": "scheduler.db",
    "USER_PROFILE_DB_PATH": "user_profiles/store.db",
    "VERA_INTERESTS_DB_PATH": "vera_interests/store.db",
    "PROFILE_GRAPH_DB_PATH": "profile_graph/store.db",
    "EXTRACT_DB_PATH": "extract/cursors.db",
    "LEARN_DB_PATH": "learn/store.db",
    "VERA_JOURNAL_PATH": "journal/JOURNAL.md",
    "VERA_MEMORY_DIR": "vera_memory",
}

for _var, _rel in _STORE_PATHS.items():
    os.environ.setdefault(_var, os.path.join(_ROOT, _rel))
