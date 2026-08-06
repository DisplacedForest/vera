import math

from . import vein_engine
from . import workflow_projection
from . import workflow_triggers


PULSE_SPECS = {
    "pulse.triage": {"label": "Gather candidates", "icon": "globe", "tint": "accent", "category": "core",
                     "description": "Pulls in this run's fresh candidate stories and signals so the rest of the pipeline has something to judge.",
                     "config_schema": {}, "insertable": False},
    "pulse.gates": {"label": "Filter by interest", "icon": "line.3.horizontal.decrease.circle", "tint": "orange",
                    "category": "core",
                    "description": "Checks each candidate against your interest and quality gates and drops the ones that fall short.",
                    "config_schema": {}, "insertable": False},
    "pulse.synthesis": {"label": "Write cards", "icon": "sparkles", "tint": "purple", "category": "core",
                        "description": "Writes each surviving candidate into a readable card with a headline and summary.",
                        "config_schema": {}, "insertable": False},
    "pulse.claim_audit": {"label": "Check the facts", "icon": "checkmark.shield", "tint": "cyan",
                          "category": "core",
                          "description": "Rechecks the factual claims in every drafted card and pulls the ones that don't hold up.",
                          "config_schema": {}, "insertable": False},
    "pulse.cover_art": {"label": "Generate covers", "icon": "photo", "tint": "purple", "category": "core",
                        "description": "Generates a cover image for each card in the visual style you pick.",
                        "config_schema": {"style": {"type": "choice", "default": "rotating",
                                                    "options": ["rotating", "photographic", "illustrated", "editorial"]}},
                        "insertable": False},
    "pulse.visual_review": {"label": "Review covers", "icon": "eye", "tint": "cyan", "category": "visual",
                            "description": "Scores each generated cover and rejects the ones below your quality threshold.",
                            "config_schema": {"threshold": {"type": "number", "min": 0, "max": 1, "default": 0.8}},
                            "insertable": False},
    "pulse.cover_retry": {"label": "Retry a cover", "icon": "arrow.clockwise", "tint": "orange", "category": "visual",
                          "description": "Regenerates a rejected cover one more time before the card ships without it.",
                          "config_schema": {"max_attempts": {"type": "choice", "options": [0, 1], "default": 1}},
                          "insertable": False},
    "pulse.inject": {"label": "Publish to feed", "icon": "arrow.down.to.line", "tint": "green", "category": "core",
                     "description": "Publishes the finished cards into your feed.",
                     "config_schema": {}, "insertable": False},
}

BLOCK_META = {
    "web_search": {"label": "Web search", "icon": "magnifyingglass", "category": "enrich",
                   "description": "Searches the web for a query and turns the results into items.",
                   "config_schema": {"query": {"type": "text", "default": ""},
                                     "max_results": {"type": "number", "min": 1, "max": 25, "default": 5}}},
    "http_fetch": {"label": "HTTP fetch", "icon": "arrow.down.circle", "category": "enrich",
                   "description": "Fetches a URL and turns the response into items.",
                   "config_schema": {"url": {"type": "text", "default": ""},
                                     "extract": {"type": "text", "default": ""},
                                     "label": {"type": "text", "default": ""}}},
    "ha_state": {"label": "Home state", "icon": "house", "category": "enrich",
                 "description": "Reads the current state of a Home Assistant entity as an item.",
                 "config_schema": {"entity_id": {"type": "text", "default": ""}}},
    "trip_band": {"label": "Trip band", "icon": "waveform.path", "category": "transform",
                  "description": "Watches a numeric reading and emits an item when it crosses the band you set.",
                  "config_schema": {"hi": {"type": "number"}, "lo": {"type": "number"},
                                    "field": {"type": "text", "default": "value"},
                                    "severity": {"type": "choice", "default": "alert",
                                                 "options": ["notice", "alert", "critical"]}}},
    "llm_judge": {"label": "LLM judge", "icon": "scale.3d", "category": "transform",
                  "description": "Asks the model whether each item clears the bar you describe and keeps the ones that do.",
                  "config_schema": {"bar": {"type": "text", "default": ""}}},
    "llm_compose": {"label": "LLM compose", "icon": "text.badge.star", "category": "transform",
                    "description": "Asks the model to write new text from the incoming items.",
                    "config_schema": {"style": {"type": "text", "default": ""}}},
    "situation_cluster": {"label": "Situation cluster", "icon": "circle.grid.2x2", "category": "transform",
                          "description": "Groups related items into one situation so they present together.",
                          "config_schema": {"deepen_query": {"type": "text", "default": ""}}},
    "present": {"label": "Present", "icon": "chart.bar", "category": "notify",
                "description": "Formats the final items into the card that gets posted.",
                "config_schema": {"stats": {"type": "text", "default": ""},
                                  "chart": {"type": "text", "default": ""}}},
}

GENERAL_SPECS = {
    "flow.filter": {"label": "Filter", "icon": "line.3.horizontal.decrease", "tint": "orange",
                    "category": "transform",
                    "description": "Keeps or drops cards by comparing one of their fields against a value you choose.",
                    "config_schema": {"field": {"type": "text", "default": "title"},
                                      "operator": {"type": "choice", "default": "contains",
                                                   "options": ["contains", "not_contains", "equals",
                                                               "not_equals", "present", "missing"]},
                                      "value": {"type": "text", "default": ""},
                                      "action": {"type": "choice", "default": "keep", "options": ["keep", "drop"]}},
                    "insertable": True},
    "flow.llm_step": {"label": "LLM step", "icon": "wand.and.stars", "tint": "purple", "category": "transform",
                      "description": "Runs your prompt over each card or the whole set and applies what the model returns.",
                      "config_schema": {"prompt": {"type": "text", "default": ""},
                                        "mode": {"type": "choice", "default": "per_card",
                                                 "options": ["per_card", "set"]},
                                        "output": {"type": "choice", "default": "annotate",
                                                   "options": ["annotate", "replace_summary", "drop_on_empty"]}},
                      "insertable": True},
    "flow.http_fetch": {"label": "HTTP fetch", "icon": "arrow.down.circle", "tint": "cyan", "category": "enrich",
                        "description": "Fetches a URL and attaches what it finds for later steps to use.",
                        "config_schema": {"url": {"type": "text", "default": ""},
                                          "extract": {"type": "text", "default": ""},
                                          "context_key": {"type": "text", "default": "fetched"}},
                        "insertable": True},
    "flow.notify": {"label": "Notify", "icon": "bell.badge", "tint": "green", "category": "notify",
                    "description": "Sends a notification with your headline when cards reach this step.",
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
    "triggers": [workflow_triggers.SCHEDULE_TYPE],
    "locked": False,
}

GENERIC_PROFILE = {"id": "generic", "spine": [], "insertable_categories": [], "pairs": [],
                   "triggers": [], "locked": False}

LOCKED_PROFILE = {"id": "locked", "spine": [], "insertable_categories": [], "pairs": [],
                  "triggers": [workflow_triggers.SCHEDULE_TYPE], "locked": True}


def profile_for(workflow_id: str) -> dict:
    if workflow_id == "pulse":
        return PULSE_PROFILE
    if workflow_projection.is_projected(workflow_id):
        return LOCKED_PROFILE
    return GENERIC_PROFILE


def block_config_schema(name: str) -> dict:
    declared = vein_engine.NODE_SPECS.get(name) or {}
    return declared.get("config_schema") or BLOCK_META.get(name, {}).get("config_schema") or {}


def _block_spec(name: str) -> dict:
    declared = vein_engine.NODE_SPECS.get(name) or {}
    meta = BLOCK_META.get(name, {})
    return {
        "label": declared.get("label") or meta.get("label") or name.replace("_", " ").title(),
        "icon": declared.get("icon") or meta.get("icon") or "puzzlepiece",
        "tint": declared.get("tint") or meta.get("tint") or "accent",
        "category": declared.get("category") or meta.get("category") or "transform",
        "description": declared.get("description") or meta.get("description")
                       or vein_engine.BLOCK_NOTES.get(name) or "",
        "config_schema": block_config_schema(name),
        "insertable": bool(declared.get("insertable", False)),
    }


def spec_for(node_type) -> dict | None:
    if node_type in workflow_triggers.SPECS:
        return workflow_triggers.SPECS[node_type]
    if node_type in PULSE_SPECS:
        return PULSE_SPECS[node_type]
    if node_type in GENERAL_SPECS:
        return GENERAL_SPECS[node_type]
    if isinstance(node_type, str) and node_type in vein_engine.BLOCKS:
        return _block_spec(node_type)
    return workflow_projection.step_spec(node_type)


def catalog() -> list[dict]:
    entries = [{"type": node_type, **spec} for node_type, spec in workflow_triggers.SPECS.items()]
    entries.extend({"type": node_type, **spec} for node_type, spec in PULSE_SPECS.items())
    entries.extend({"type": node_type, **spec} for node_type, spec in GENERAL_SPECS.items())
    entries.extend({"type": name, **_block_spec(name)} for name in sorted(vein_engine.BLOCKS))
    entries.extend({"type": node_type, **spec}
                   for node_type, spec in sorted(workflow_projection.catalog_step_specs().items()))
    for entry in entries:
        description = entry.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"catalog entry {entry['type']} is missing a description")
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


def _validate_triggers(nodes: list[dict], edges: list[dict]) -> set[str]:
    triggers = [node for node in nodes if workflow_triggers.is_trigger(node.get("type"))]
    if len(triggers) > 1:
        raise ValueError("only one trigger is allowed in a workflow")
    trigger_ids = {node["id"] for node in triggers}
    for edge in edges:
        if edge.get("to") in trigger_ids:
            raise ValueError("a trigger starts the workflow and cannot take an input")
    for node in triggers:
        workflow_triggers.validate_config(node.get("config") or {})
    return trigger_ids


def _without(nodes: list[dict], edges: list[dict], ids: set[str]):
    kept_nodes = [node for node in nodes if node["id"] not in ids]
    kept_edges = [edge for edge in edges if edge["from"] not in ids and edge["to"] not in ids]
    return kept_nodes, kept_edges


def _validate_trigger_edges(trigger_ids: set[str], edges: list[dict], head: str):
    for trigger_id in trigger_ids:
        outgoing = [edge for edge in edges if edge["from"] == trigger_id]
        if len(outgoing) != 1 or outgoing[0]["to"] != head:
            raise ValueError("the trigger must connect to the start of the workflow")


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
    path = _single_path(nodes, edges)
    order = [type_for[node_id] for node_id in path]
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
    return path


def _validate_locked(workflow_id: str, nodes: list[dict], edges: list[dict]):
    projection = workflow_projection.definition_for(workflow_id)
    if projection is None:
        raise ValueError("this workflow's structure is managed by the server")
    served_nodes = {(node["id"], node["type"]) for node in projection["nodes"]}
    served_edges = {(edge["from"], edge["to"]) for edge in projection["edges"]}
    got_nodes = {(node.get("id"), node.get("type")) for node in nodes}
    got_edges = {(edge.get("from"), edge.get("to")) for edge in edges}
    if served_nodes != got_nodes or served_edges != got_edges:
        raise ValueError("this workflow's steps are managed by the server; only its settings can change")
    served_config = {node["id"]: node.get("config") or {} for node in projection["nodes"]}
    for node in nodes:
        if workflow_triggers.is_trigger(node.get("type")):
            continue
        if (node.get("config") or {}) != served_config.get(node.get("id"), {}):
            raise ValueError("this workflow's step settings come from its definition; only its schedule can change here")


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
    pairs = [(edge.get("from"), edge.get("to")) for edge in edges]
    if len(pairs) != len(set(pairs)):
        raise ValueError("the same connection appears more than once")
    trigger_ids = _validate_triggers(nodes, edges)
    profile = profile_for(workflow_id)
    if profile.get("locked"):
        _validate_locked(workflow_id, nodes, edges)
    if profile["spine"]:
        body_nodes, body_edges = _without(nodes, edges, trigger_ids)
        if not body_nodes:
            raise ValueError("workflow needs its processing stages, not just a trigger")
        path = _validate_spine(profile, body_nodes, body_edges)
        _validate_trigger_edges(trigger_ids, edges, path[0])
    else:
        _require_acyclic(ids, edges)


def validate_promotion(workflow_id: str, definition: dict):
    validate_definition(workflow_id, definition)
    present = {node.get("type") for node in definition.get("nodes") or []}
    for trigger_type in profile_for(workflow_id).get("triggers") or []:
        if trigger_type not in present:
            label = spec_for(trigger_type)["label"]
            raise ValueError(f"Add a {label} trigger so this workflow can start on its own")
