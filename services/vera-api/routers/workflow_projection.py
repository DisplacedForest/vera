import json
import math

from . import workflow_triggers


FLOW_FACE: dict[str, dict] = {
    "pulse":          {"label": "Pulse briefing", "icon": "newspaper", "tint": "accent",
                       "group": "Ambient", "feeds": ["pulse_feed"],
                       "tools": ["websearch", "vera-image"],
                       "description": "Gathers fresh stories on its schedule, filters them by your interests, writes them into cards, and publishes your briefing."},
    "home_model":     {"label": "Home model", "icon": "house", "tint": "cyan",
                       "group": "Home", "feeds": ["actions"], "tools": [],
                       "description": "Refreshes the model of your home from recent activity so predictions stay current."},
    "home_reconcile": {"label": "Map reconcile", "icon": "checklist", "tint": "cyan",
                       "group": "Home", "feeds": ["veins"], "tools": [],
                       "description": "Compares the home map against what devices actually reported and flags drift."},
    "home_digest":    {"label": "Rhythm digest", "icon": "doc.text", "tint": "cyan",
                       "group": "Home", "feeds": ["veins"], "tools": [],
                       "description": "Summarizes the house's recent rhythm into a short daily digest."},
    "conversation_extract": {"label": "Conversation extraction", "icon": "text.bubble",
                             "tint": "purple", "group": "Memory", "feeds": ["memory"], "tools": [],
                             "description": "Reads recent conversations and files what matters into your profile graph."},
    "weight_fit":     {"label": "Weight fit", "icon": "chart.xyaxis.line", "tint": "purple",
                       "group": "Memory", "feeds": ["memory"], "tools": [],
                       "description": "Refits the story ranking weights from your feedback so scoring tracks what you actually read."},
    "knowledge_groom": {"label": "Knowledge groom", "icon": "archivebox", "tint": "cyan",
                        "group": "Home", "feeds": ["pulse_feed"], "tools": [],
                        "description": "Tidies the knowledge store by merging duplicates and retiring stale entries."},
}

DEFAULT_FACE = {"icon": "clock", "tint": "gray", "group": "Other", "feeds": [], "tools": [],
                "description": "A scheduled background flow."}

STEP_SPECS: dict[str, dict] = {
    "step.home_model": {"label": "Refresh the home model", "icon": "house", "tint": "cyan",
                        "category": "core",
                        "description": "Rebuilds the model of your home from the latest recorded activity.",
                        "config_schema": {}, "insertable": False},
    "step.home_reconcile": {"label": "Reconcile the map", "icon": "checklist", "tint": "cyan",
                            "category": "core",
                            "description": "Compares the home map against what devices actually reported and notes any drift.",
                            "config_schema": {}, "insertable": False},
    "step.home_digest": {"label": "Write the rhythm digest", "icon": "doc.text", "tint": "cyan",
                         "category": "core",
                         "description": "Summarizes the house's recent rhythm into a short digest.",
                         "config_schema": {}, "insertable": False},
    "step.conversation_extract": {"label": "Extract from conversations", "icon": "text.bubble",
                                  "tint": "purple", "category": "core",
                                  "description": "Reads recent conversations and files what matters into your profile graph.",
                                  "config_schema": {}, "insertable": False},
    "step.weight_fit": {"label": "Refit ranking weights", "icon": "chart.xyaxis.line",
                        "tint": "purple", "category": "core",
                        "description": "Refits the story ranking weights from your recent feedback.",
                        "config_schema": {}, "insertable": False},
    "step.knowledge_groom": {"label": "Groom the knowledge store", "icon": "archivebox",
                             "tint": "cyan", "category": "core",
                             "description": "Tidies the knowledge store by merging duplicates and retiring stale entries.",
                             "config_schema": {}, "insertable": False},
}

_REGISTERED_STEPS: dict[str, list[dict]] = {}


def register_steps(job_id: str, steps: list[dict]):
    _REGISTERED_STEPS[job_id] = [dict(step) for step in steps]


def _registry() -> dict:
    from . import scheduler
    return scheduler._registry()


def is_projected(workflow_id) -> bool:
    if not isinstance(workflow_id, str) or workflow_id == "pulse":
        return False
    return workflow_id in _registry()


def _vein_face(job_id: str) -> dict:
    from . import pulse_veins
    spec = pulse_veins.manifest(job_id.removeprefix("vein_")) or {}
    return {"label": spec.get("label", job_id), "icon": spec.get("icon", "clock"),
            "tint": "cyan", "group": "Ambient", "feeds": ["veins"], "tools": [],
            "description": spec.get("blurb") or "An ambient watch that posts a card when something needs you."}


def face_for(job_id: str) -> dict:
    face = FLOW_FACE.get(job_id)
    if face is not None:
        return face
    if job_id.startswith("vein_"):
        return _vein_face(job_id)
    return DEFAULT_FACE


def _fallback_step_spec(job_id: str, label: str) -> dict:
    face = face_for(job_id)
    return {"label": label, "icon": face["icon"], "tint": face["tint"], "category": "core",
            "description": f"Runs the {label} job.", "config_schema": {}, "insertable": False}


def step_spec(step_type: str) -> dict | None:
    if step_type in STEP_SPECS:
        return STEP_SPECS[step_type]
    if not isinstance(step_type, str) or not step_type.startswith("step."):
        return None
    job_id = step_type.removeprefix("step.")
    registry = _registry()
    if job_id not in registry:
        return None
    return _fallback_step_spec(job_id, registry[job_id][0])


def catalog_step_specs() -> dict[str, dict]:
    out = dict(STEP_SPECS)
    for job_id, (label, _cron, _handler) in _registry().items():
        if job_id == "pulse" or job_id.startswith("vein_"):
            continue
        step_type = f"step.{job_id}"
        if step_type not in out:
            out[step_type] = _fallback_step_spec(job_id, label)
    return out


def _step_config(params: dict, schema: dict) -> dict:
    config = {}
    for key, value in params.items():
        field = schema.get(key)
        if not field:
            continue
        kind = field.get("type")
        if kind == "text" and not isinstance(value, str):
            value = json.dumps(value)
        if kind == "number":
            low, high = field.get("min"), field.get("max")
            numeric = (isinstance(value, (int, float)) and not isinstance(value, bool)
                       and math.isfinite(value))
            if (not numeric or (low is not None and value < low)
                    or (high is not None and value > high)):
                continue
        if kind == "choice" and not any(type(value) is type(option) and value == option
                                        for option in field.get("options") or []):
            continue
        config[key] = value
    return config


def _vein_steps(job_id: str) -> list[dict]:
    from . import pulse_veins, workflow_registry
    defn = pulse_veins.manifest(job_id.removeprefix("vein_")) or {}
    steps = []
    for index, entry in enumerate(defn.get("pipeline") or [], start=1):
        block = entry.get("block")
        config = _step_config(entry.get("params") or {}, workflow_registry.block_config_schema(block))
        step = {"id": f"step-{index}", "type": block}
        if config:
            step["config"] = config
        steps.append(step)
    return steps


def definition_for(workflow_id) -> dict | None:
    if not isinstance(workflow_id, str) or workflow_id == "pulse":
        return None
    registry = _registry()
    if workflow_id not in registry:
        return None
    if workflow_id.startswith("vein_"):
        steps = _vein_steps(workflow_id)
    else:
        steps = _REGISTERED_STEPS.get(workflow_id) or [{"id": "run", "type": f"step.{workflow_id}"}]
    if not steps:
        return None
    from . import scheduler
    cron = scheduler.job_cron(workflow_id)
    config = workflow_triggers.config_for(cron)
    trigger = {"id": "schedule", "type": workflow_triggers.SCHEDULE_TYPE}
    if config is not None:
        trigger["config"] = config
    elif cron:
        trigger["config"] = {}
        trigger["rule"] = cron
    else:
        trigger["config"] = dict(workflow_triggers.DEFAULT_CONFIG)
    nodes = [trigger, *steps]
    edges = [{"from": "schedule", "to": steps[0]["id"]}]
    edges.extend({"from": steps[index]["id"], "to": steps[index + 1]["id"]}
                 for index in range(len(steps) - 1))
    return {"id": workflow_id, "label": face_for(workflow_id)["label"], "layout": "pipeline",
            "nodes": nodes, "edges": edges}
