from . import vein_engine
from . import workflow_registry


NODE_IMPLS: dict[str, dict] = {}


def register(node_type: str, run=None, *, pull=None, barrier: bool = False):
    NODE_IMPLS[node_type] = {"run": run, "pull": pull, "barrier": barrier}


class RunContext:
    def __init__(self, definition: dict, run_id: str | None = None,
                 data: dict | None = None, template: dict | None = None):
        self.definition = definition
        self.run_id = run_id
        self.data = data if data is not None else {}
        self.template = template if template is not None else {}
        self.summaries: dict[str, dict] = {}


class _NodeState:
    def __init__(self, node: dict, impl: dict):
        self.node = node
        self.impl = impl
        self.row = None
        self.opened = False
        self.items_in = 0
        self.items_out = 0
        self.buffer: list = []
        self.state = "ok"
        self.error: str | None = None


async def _block_run(node: dict, items: list, ctx: RunContext) -> list:
    runner = vein_engine.BLOCKS[node["type"]]
    out = await runner(list(items), dict(node.get("config") or {}), dict(ctx.template))
    return list(out or [])


def _impl_for(node: dict) -> dict:
    spec = NODE_IMPLS.get(node["type"])
    if spec:
        return spec
    if node["type"] in vein_engine.BLOCKS:
        return {"run": _block_run, "pull": None, "barrier": False}
    raise ValueError(f"no implementation registered for node type {node['type']}")


def _order(definition: dict):
    nodes = {n["id"]: n for n in definition.get("nodes") or []}
    upstream = {nid: [] for nid in nodes}
    downstream = {nid: [] for nid in nodes}
    indegree = {nid: 0 for nid in nodes}
    for edge in definition.get("edges") or []:
        upstream[edge["to"]].append(edge["from"])
        downstream[edge["from"]].append(edge["to"])
        indegree[edge["to"]] += 1
    ready = [n["id"] for n in definition.get("nodes") or [] if indegree[n["id"]] == 0]
    ordered = []
    while ready:
        nid = ready.pop(0)
        ordered.append(nodes[nid])
        for target in downstream[nid]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if len(ordered) != len(nodes):
        raise ValueError("workflow must not contain a cycle")
    return ordered, upstream, downstream


async def execute(definition: dict, ctx: RunContext, recorder=None) -> list:
    ordered, upstream, downstream = _order(definition)
    if not ordered:
        return []
    profile = workflow_registry.profile_for(definition.get("id"))
    core = set(profile["spine"]) | {t for pair in profile["pairs"] for t in pair["types"]}

    def _optional(node: dict) -> bool:
        return bool(profile["spine"]) and node["type"] not in core

    states = {n["id"]: _NodeState(n, _impl_for(n)) for n in ordered}

    def _open(st: _NodeState, input_summary: dict | None = None):
        if st.opened:
            return
        st.opened = True
        if recorder is not None and ctx.run_id:
            st.row = recorder.start_node(ctx.run_id, st.node["id"], input=input_summary)

    def _close(st: _NodeState):
        if st.row is not None:
            output = {"items": st.items_out, **ctx.summaries.get(st.node["id"], {})}
            recorder.finish_node(st.row, st.state, output, st.error,
                                 input={"items": st.items_in})
            st.row = None

    async def _invoke(st: _NodeState, items: list) -> list:
        try:
            out = list((await st.impl["run"](st.node, items, ctx)) or [])
        except Exception as e:
            if not _optional(st.node):
                st.state = "error"
                st.error = str(e)
                _close(st)
                raise
            st.state = "warning"
            st.error = str(e)
            out = list(items)
        st.items_out += len(out)
        return out

    source = states[ordered[0]["id"]] if ordered[0]["id"] in states else None
    if source is not None and source.impl.get("pull"):
        path = [n["id"] for n in ordered]

        async def _flow(index: int, items: list):
            for pos in range(index, len(path)):
                st = states[path[pos]]
                if st.impl["barrier"]:
                    st.items_in += len(items)
                    st.buffer.extend(items)
                    return
                st.items_in += len(items)
                _open(st)
                items = await _invoke(st, items)

        _open(source)
        while True:
            batch = await source.impl["pull"](source.node, ctx)
            if batch is None:
                break
            batch = list(batch)
            source.items_out += len(batch)
            await _flow(1, batch)
        _close(source)
        for pos in range(1, len(path)):
            st = states[path[pos]]
            if st.impl["barrier"]:
                items = st.buffer
                st.buffer = []
                _open(st, {"items": len(items)})
                out = await _invoke(st, items)
                _close(st)
                await _flow(pos + 1, out)
            else:
                _open(st)
                _close(st)
        return []

    outputs: dict[str, list] = {}
    terminal: list = []
    for node in ordered:
        st = states[node["id"]]
        items = [item for up in upstream[node["id"]] for item in outputs.get(up, [])]
        st.items_in = len(items)
        _open(st, {"items": len(items)})
        out = await _invoke(st, items)
        _close(st)
        outputs[node["id"]] = out
        if not downstream[node["id"]]:
            terminal.extend(out)
    return terminal
