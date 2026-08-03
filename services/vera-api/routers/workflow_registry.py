import math

from . import vein_engine


PULSE_SPECS = {
    "pulse.triage": {"label": "Triage", "icon": "globe", "tint": "accent", "category": "core",
                     "config_schema": {}, "insertable": False},
    "pulse.gates": {"label": "Gates", "icon": "line.3.horizontal.decrease.circle", "tint": "orange",
                    "category": "core", "config_schema": {}, "insertable": False},
    "pulse.synthesis": {"label": "Synthesis", "icon": "sparkles", "tint": "purple", "category": "core",
                        "config_schema": {}, "insertable": False},
    "pulse.claim_audit": {"label": "Claim audit", "icon": "checkmark.shield", "tint": "cyan",
                          "category": "core", "config_schema": {}, "insertable": False},
    "pulse.cover_art": {"label": "Cover art", "icon": "photo", "tint": "purple", "category": "core",
                        "config_schema": {"style": {"type": "choice", "default": "rotating",
                                                    "options": ["rotating", "photographic", "illustrated", "editorial"]}},
                        "insertable": False},
    "pulse.visual_review": {"label": "Visual review", "icon": "eye", "tint": "cyan", "category": "visual",
                            "config_schema": {"threshold": {"type": "number", "min": 0, "max": 1, "default": 0.8}},
                            "insertable": False},
    "pulse.cover_retry": {"label": "One retry", "icon": "arrow.clockwise", "tint": "orange", "category": "visual",
                          "config_schema": {"max_attempts": {"type": "choice", "options": [0, 1], "default": 1}},
                          "insertable": False},
    "pulse.inject": {"label": "Inject", "icon": "arrow.down.to.line", "tint": "green", "category": "core",
                     "config_schema": {}, "insertable": False},
}

BLOCK_META = {
    "web_search": {"label": "Web search", "icon": "magnifyingglass", "category": "enrich"},
    "http_fetch": {"label": "HTTP fetch", "icon": "arrow.down.circle", "category": "enrich"},
    "ha_state": {"label": "Home state", "icon": "house", "category": "enrich"},
    "trip_band": {"label": "Trip band", "icon": "waveform.path", "category": "transform"},
    "llm_judge": {"label": "LLM judge", "icon": "scale.3d", "category": "transform"},
    "llm_compose": {"label": "LLM compose", "icon": "text.badge.star", "category": "transform"},
    "situation_cluster": {"label": "Situation cluster", "icon": "circle.grid.2x2", "category": "transform"},
    "present": {"label": "Present", "icon": "chart.bar", "category": "notify"},
}

GENERAL_SPECS = {
    "flow.filter": {"label": "Filter", "icon": "line.3.horizontal.decrease", "tint": "orange",
                    "category": "transform",
                    "config_schema": {"field": {"type": "text", "default": "title"},
                                      "operator": {"type": "choice", "default": "contains",
                                                   "options": ["contains", "not_contains", "equals",
                                                               "not_equals", "present", "missing"]},
                                      "value": {"type": "text", "default": ""},
                                      "action": {"type": "choice", "default": "keep", "options": ["keep", "drop"]}},
                    "insertable": True},
    "flow.llm_step": {"label": "LLM step", "icon": "wand.and.stars", "tint": "purple", "category": "transform",
                      "config_schema": {"prompt": {"type": "text", "default": ""},
                                        "mode": {"type": "choice", "default": "per_card",
                                                 "options": ["per_card", "set"]},
                                        "output": {"type": "choice", "default": "annotate",
                                                   "options": ["annotate", "replace_summary", "drop_on_empty"]}},
                      "insertable": True},
    "flow.http_fetch": {"label": "HTTP fetch", "icon": "arrow.down.circle", "tint": "cyan", "category": "enrich",
                        "config_schema": {"url": {"type": "text", "default": ""},
                                          "extract": {"type": "text", "default": ""},
                                          "context_key": {"type": "text", "default": "fetched"}},
                        "insertable": True},
    "flow.notify": {"label": "Notify", "icon": "bell.badge", "tint": "green", "category": "notify",
                    "config_schema": {"headline": {"type": "text", "default": ""}},
                    "insertable": True},
}

PULSE_PROFILE = {
    "id": "pulse",
    "spine": ["pulse.triage", "pulse.gates", "pulse.synthesis", "pulse.claim_audit",
              "pulse.cover_art", "pulse.inject"],
    "insertable_categories": ["transform", "enrich", "notify"],
    "pairs": [{"types": ["pulse.visual_review", "pulse.cover_retry"],
               "after": "pulse.cover_art", "before": "pulse.inject"}],
}

GENERIC_PROFILE = {"id": "generic", "spine": [], "insertable_categories": [], "pairs": []}


def profile_for(workflow_id: str) -> dict:
    return PULSE_PROFILE if workflow_id == "pulse" else GENERIC_PROFILE


def _block_spec(name: str) -> dict:
    declared = vein_engine.NODE_SPECS.get(name) or {}
    meta = BLOCK_META.get(name, {})
    return {
        "label": declared.get("label") or meta.get("label") or name.replace("_", " ").title(),
        "icon": declared.get("icon") or meta.get("icon") or "puzzlepiece",
        "tint": declared.get("tint") or meta.get("tint") or "accent",
        "category": declared.get("category") or meta.get("category") or "transform",
        "config_schema": declared.get("config_schema") or {},
        "insertable": bool(declared.get("insertable", False)),
    }


def spec_for(node_type) -> dict | None:
    if node_type in PULSE_SPECS:
        return PULSE_SPECS[node_type]
    if node_type in GENERAL_SPECS:
        return GENERAL_SPECS[node_type]
    if isinstance(node_type, str) and node_type in vein_engine.BLOCKS:
        return _block_spec(node_type)
    return None


def catalog() -> list[dict]:
    entries = [{"type": node_type, **spec} for node_type, spec in PULSE_SPECS.items()]
    entries.extend({"type": node_type, **spec} for node_type, spec in GENERAL_SPECS.items())
    entries.extend({"type": name, **_block_spec(name)} for name in sorted(vein_engine.BLOCKS))
    return entries


def _validate_config(node: dict, spec: dict):
    config = node.get("config", {})
    if not isinstance(config, dict):
        raise ValueError("node configuration must be an object")
    schema = spec["config_schema"]
    label = spec["label"]
    for key, value in config.items():
        field = schema.get(key)
        if not field:
            raise ValueError(f"{label} does not accept a {key} setting")
        if field["type"] == "choice":
            if not any(type(value) is type(option) and value == option for option in field["options"]):
                options = ", ".join(str(option) for option in field["options"])
                raise ValueError(f"{label} {key} must be one of {options}")
        if field["type"] == "text":
            if not isinstance(value, str):
                raise ValueError(f"{label} {key} must be text")
        if field["type"] == "number":
            low, high = field.get("min"), field.get("max")
            numeric = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            if not numeric or (low is not None and value < low) or (high is not None and value > high):
                raise ValueError(f"{label} {key} must be a number between {low} and {high}")


def _single_path(nodes: list[dict], edges: list[dict]) -> list[str]:
    outgoing: dict[str, str] = {}
    indegree = {node["id"]: 0 for node in nodes}
    seen = set()
    for edge in edges:
        key = (edge["from"], edge["to"])
        if key in seen or edge["from"] in outgoing:
            raise ValueError("workflow must be a single connected path")
        seen.add(key)
        outgoing[edge["from"]] = edge["to"]
        indegree[edge["to"]] += 1
    starts = [node_id for node_id, count in indegree.items() if count == 0]
    if len(starts) != 1 or any(count > 1 for count in indegree.values()):
        raise ValueError("workflow must be a single connected path")
    path = [starts[0]]
    while path[-1] in outgoing:
        path.append(outgoing[path[-1]])
        if len(path) > len(nodes):
            raise ValueError("workflow must be a single connected path")
    if len(path) != len(nodes):
        raise ValueError("workflow must be a single connected path")
    return path


def _require_acyclic(ids: list[str], edges: list[dict]):
    indegree = {node_id: 0 for node_id in ids}
    downstream: dict[str, list[str]] = {node_id: [] for node_id in ids}
    for edge in edges:
        downstream[edge["from"]].append(edge["to"])
        indegree[edge["to"]] += 1
    ready = [node_id for node_id, count in indegree.items() if count == 0]
    visited = 0
    while ready:
        current = ready.pop()
        visited += 1
        for target in downstream[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if visited != len(ids):
        raise ValueError("workflow must not contain a cycle")


def _validate_spine(profile: dict, nodes: list[dict], edges: list[dict]):
    counts: dict[str, int] = {}
    for node in nodes:
        counts[node["type"]] = counts.get(node["type"], 0) + 1
    for stage in profile["spine"]:
        if not counts.get(stage):
            raise ValueError(f"workflow is missing {PULSE_SPECS[stage]['label']}")
        if counts[stage] > 1:
            raise ValueError(f"workflow contains {PULSE_SPECS[stage]['label']} more than once")
    pair_types = {pair_type for pair in profile["pairs"] for pair_type in pair["types"]}
    for pair in profile["pairs"]:
        present = [pair_type for pair_type in pair["types"] if counts.get(pair_type)]
        if present and len(present) != len(pair["types"]):
            raise ValueError("visual review and retry must be added together")
        for pair_type in pair["types"]:
            if counts.get(pair_type, 0) > 1:
                raise ValueError(f"workflow contains {PULSE_SPECS[pair_type]['label']} more than once")
    for node in nodes:
        if node["type"] in profile["spine"] or node["type"] in pair_types:
            continue
        spec = spec_for(node["type"])
        if not spec["insertable"] or spec["category"] not in profile["insertable_categories"]:
            raise ValueError(f"{spec['label']} cannot be added to this workflow")
    type_for = {node["id"]: node["type"] for node in nodes}
    order = [type_for[node_id] for node_id in _single_path(nodes, edges)]
    if order[0] != profile["spine"][0] or order[-1] != profile["spine"][-1]:
        raise ValueError(f"workflow must run from {PULSE_SPECS[profile['spine'][0]]['label']} to {PULSE_SPECS[profile['spine'][-1]]['label']}")
    if [stage for stage in order if stage in profile["spine"]] != profile["spine"]:
        raise ValueError("core stages are out of order")
    for pair in profile["pairs"]:
        first, second = pair["types"]
        if first not in order:
            continue
        start, end = order.index(first), order.index(second)
        if end != start + 1:
            raise ValueError(f"{PULSE_SPECS[second]['label']} must directly follow {PULSE_SPECS[first]['label']}")
        if not (order.index(pair["after"]) < start and end < order.index(pair["before"])):
            raise ValueError(f"{PULSE_SPECS[first]['label']} belongs between {PULSE_SPECS[pair['after']]['label']} and {PULSE_SPECS[pair['before']]['label']}")


def validate_definition(workflow_id: str, definition: dict):
    nodes = definition.get("nodes")
    edges = definition.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list) or not nodes:
        raise ValueError("nodes and edges are required")
    if any(not isinstance(node, dict) for node in nodes):
        raise ValueError("nodes must be objects")
    if any(not isinstance(edge, dict) for edge in edges):
        raise ValueError("edges must be objects")
    ids = [node.get("id") for node in nodes]
    if any(not isinstance(node_id, str) or not node_id for node_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("nodes need unique ids")
    for node in nodes:
        spec = spec_for(node.get("type"))
        if not spec:
            raise ValueError("workflow uses an unsupported node type")
        _validate_config(node, spec)
    declared = set(ids)
    if any(edge.get("from") not in declared or edge.get("to") not in declared for edge in edges):
        raise ValueError("edges must connect declared nodes")
    profile = profile_for(workflow_id)
    if profile["spine"]:
        _validate_spine(profile, nodes, edges)
    else:
        _require_acyclic(ids, edges)
