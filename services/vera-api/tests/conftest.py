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

from data_paths import STORE_PATHS

_ROOT = tempfile.mkdtemp(prefix="vera-api-tests-")

for _var, _rel in STORE_PATHS.items():
    os.environ.setdefault(_var, os.path.join(_ROOT, _rel))
