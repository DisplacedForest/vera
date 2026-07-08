"""Vein definition origins. Two sources merge into the catalog:

  shipped   JSON files in the repo's `veins/` directory (env `VEINS_SHIPPED_DIR`),
            validated once at first load — a bad shipped file is a startup error
  custom    one file per vein at `<VEINS_D_PATH>/<kind>.json`, scanned on every
            read so external edits show up live; a bad custom file is skipped
            with a warning and reported via `load_report`, never fatal

A custom definition never shadows a shipped kind: colliding files are skipped at
load and rejected at save. Writes are atomic (tmp file + rename)."""

import json
import logging
import os
import tempfile

from . import vein_schema

log = logging.getLogger("vera.veins")

SHIPPED_DIR = os.environ.get(
    "VEINS_SHIPPED_DIR",
    os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "veins")))
CUSTOM_DIR = os.environ.get("VEINS_D_PATH", "/data/veins.d")

SHIPPED_ORDER = ("status", "weather", "signals", "media")

_shipped_cache: list[dict] | None = None
_last_report: list[dict] = []


def shipped() -> list[dict]:
    global _shipped_cache
    if _shipped_cache is None:
        defs = []
        for name in sorted(os.listdir(SHIPPED_DIR)):
            if not name.endswith(".json") or name.endswith(".pipeline.json"):
                continue
            path = os.path.join(SHIPPED_DIR, name)
            try:
                with open(path, encoding="utf-8") as f:
                    defs.append(vein_schema.validate_definition(json.load(f)))
            except (OSError, ValueError) as e:
                raise RuntimeError(f"shipped vein definition {path} is invalid: {e}") from e
        defs.sort(key=lambda d: d["order"])
        _shipped_cache = defs
    return _shipped_cache


def shipped_kinds() -> set[str]:
    return {d["kind"] for d in shipped()}


def customs() -> dict[str, dict]:
    global _last_report
    out: dict[str, dict] = {}
    report: list[dict] = []
    try:
        names = sorted(os.listdir(CUSTOM_DIR))
    except OSError:
        _last_report = []
        return out
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(CUSTOM_DIR, name)
        try:
            with open(path, encoding="utf-8") as f:
                d = vein_schema.validate_definition(json.load(f))
            if d["kind"] in shipped_kinds():
                raise ValueError(f"kind '{d['kind']}' is shipped")
            out[d["kind"]] = d
        except (OSError, ValueError) as e:
            log.warning("skipping custom vein definition %s: %s", path, e)
            report.append({"file": path, "error": str(e)})
    _last_report = report
    return out


def load_report() -> list[dict]:
    return list(_last_report)


def save_custom(raw: dict) -> dict:
    if "order" not in raw:
        taken = [d["order"] for d in shipped()] + [d["order"] for d in customs().values()]
        raw = {**raw, "order": max(taken, default=0) + 1}
    d = vein_schema.validate_definition(raw)
    if d["kind"] in shipped_kinds():
        raise ValueError(f"kind '{d['kind']}' is shipped and read-only")
    os.makedirs(CUSTOM_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=CUSTOM_DIR, prefix=".vein-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, os.path.join(CUSTOM_DIR, f"{d['kind']}.json"))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return d


def delete_custom(kind: str) -> bool:
    try:
        os.unlink(os.path.join(CUSTOM_DIR, f"{kind}.json"))
        return True
    except OSError:
        return False
