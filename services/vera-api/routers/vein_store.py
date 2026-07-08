"""Vein state store — which Pulse veins a deployment runs, in one JSON document.

The vein CATALOG (what veins exist, their producers, provider slots, and option
groups) lives in pulse_veins.py; this store holds only what a deployment chooses at
runtime: per-vein `enabled`, option values, and provider config. Nothing is enabled
by default — an empty store means an empty chip row.

One-time seeding: on the first load of a deployment whose producers are demonstrably
configured (home coordinates set, integrations enabled), those veins seed enabled so
an upgrade keeps its chip row; a genuinely fresh install seeds nothing.

Writes are atomic (tmp file + rename) so a crash mid-save never corrupts the store.
"""

import json
import os
import tempfile
import threading

PATH = os.environ.get("VEINS_PATH", "/data/veins.json")
_lock = threading.Lock()


def _adopt_legacy() -> None:
    """Adopt a legacy `lanes.json` in the data volume as the vein store when the vein
    store does not exist yet (same volume, atomic)."""
    legacy = os.path.join(os.path.dirname(PATH) or ".", "lanes.json")
    if os.path.exists(PATH) or not os.path.exists(legacy):
        return
    try:
        os.replace(legacy, PATH)
    except OSError:
        pass


def _read() -> dict:
    _adopt_legacy()
    try:
        with open(PATH, encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(doc: dict) -> None:
    d = os.path.dirname(PATH) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".veins-")
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
            from . import pulse_veins
            for kind, state in pulse_veins.seed_states().items():
                doc.setdefault(kind, {}).update(state)
            doc["_seeded"] = True
            _save(doc)
        return doc


def remove(kind: str) -> None:
    """Drop one vein's runtime state entirely."""
    with _lock:
        doc = _read()
        if kind in doc:
            del doc[kind]
            _save(doc)


def update(kind: str, *, enabled: bool | None = None, options: dict | None = None,
           providers: dict | None = None) -> None:
    """Merge one vein's runtime state."""
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
