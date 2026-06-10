"""Integration store — runtime plugin config that survives restarts, in one JSON document.

The integration REGISTRY (what exists, its fields, its experimental features) lives in
integrations.py; this store holds only what a deployment changes at runtime: field
values entered through the API, enable/disable flags, and experimental-feature state
(enabled + when its ramifications were acknowledged). Env vars win over values here
at resolution time — env is for headless installs, this file is for live edits.

Writes are atomic (tmp file + rename) so a crash mid-save never corrupts the store.
"""

import json
import os
import tempfile
import threading

PATH = os.environ.get("INTEGRATIONS_PATH", "/data/integrations.json")
_lock = threading.Lock()


def load() -> dict:
    """The whole document: {integration_id: {fields: {..}, enabled: bool|None,
    features: {feature_id: {enabled: bool, acked_at: float|None}}}}. Empty when unset."""
    try:
        with open(PATH, encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(doc: dict) -> None:
    d = os.path.dirname(PATH) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".integrations-")
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


def update(iid: str, *, fields: dict | None = None, enabled: bool | None = None) -> None:
    """Merge field values and/or the enabled flag for one integration."""
    with _lock:
        doc = load()
        row = doc.setdefault(iid, {})
        if fields:
            row.setdefault("fields", {}).update(fields)
        if enabled is not None:
            row["enabled"] = bool(enabled)
        _save(doc)


def update_feature(iid: str, fid: str, *, enabled: bool, acked_at: float | None = None) -> None:
    """Set one experimental feature's state; a non-None acked_at records first consent."""
    with _lock:
        doc = load()
        feat = doc.setdefault(iid, {}).setdefault("features", {}).setdefault(fid, {})
        feat["enabled"] = bool(enabled)
        if acked_at is not None:
            feat["acked_at"] = acked_at
        _save(doc)
