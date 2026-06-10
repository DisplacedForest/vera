"""Lane state store — which Pulse lanes a deployment runs, in one JSON document.

The lane CATALOG (what lanes exist, their producers, provider slots, and option
groups) lives in pulse_lanes.py; this store holds only what a deployment chooses at
runtime: per-lane `enabled`, option values, and provider config. Nothing is enabled
by default — an empty store means an empty chip row.

One-time seeding: on the first load of a deployment whose producers are demonstrably
configured (home coordinates set, integrations enabled), those lanes seed enabled so
an upgrade keeps its chip row; a genuinely fresh install seeds nothing.

Writes are atomic (tmp file + rename) so a crash mid-save never corrupts the store.
"""

import json
import os
import tempfile
import threading

PATH = os.environ.get("LANES_PATH", "/data/lanes.json")
_lock = threading.Lock()


def _read() -> dict:
    try:
        with open(PATH, encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(doc: dict) -> None:
    d = os.path.dirname(PATH) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".lanes-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2)
        os.replace(tmp, PATH)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load() -> dict:
    """The whole document: {kind: {enabled: bool, options: {..}, providers: {..}},
    "_seeded": true}. Runs the one-time seeding pass on first load."""
    with _lock:
        doc = _read()
        if not doc.get("_seeded"):
            from . import pulse_lanes
            for kind, state in pulse_lanes.seed_states().items():
                doc.setdefault(kind, {}).update(state)
            doc["_seeded"] = True
            _save(doc)
        return doc


def update(kind: str, *, enabled: bool | None = None, options: dict | None = None,
           providers: dict | None = None) -> None:
    """Merge one lane's runtime state."""
    with _lock:
        doc = _read()
        row = doc.setdefault(kind, {})
        if enabled is not None:
            row["enabled"] = bool(enabled)
        if options:
            row.setdefault("options", {}).update(options)
        if providers:
            row.setdefault("providers", {}).update(providers)
        doc.setdefault("_seeded", True)  # an explicit edit is a configured deployment
        _save(doc)
