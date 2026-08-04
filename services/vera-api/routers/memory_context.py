from . import profile_graph_store as pg
from . import user_profile_store as up
from .identity import owner_id

MAX_MEMORIES = 20


def _profile_lines(user_id: str) -> list[str]:
    prof = up.get(user_id)
    lines = []
    if prof.get("persona"):
        lines.append(prof["persona"].strip())
    return lines


def _graph_lines(limit: int) -> list[str]:
    try:
        nodes = [n for n in pg.all_nodes() if n.get("type") != "thread"]
    except Exception:
        return []
    nodes.sort(key=pg.engagement_now, reverse=True)
    lines = []
    for n in nodes:
        for f in (n.get("facts") or []):
            text = f.get("text") if isinstance(f, dict) else str(f)
            if not text:
                continue
            lines.append(f"{n['label']}: {text}")
            if len(lines) >= limit:
                return lines
    return lines


async def native_memories(user_id: str | None = None) -> list[dict]:
    uid = user_id or owner_id()
    lines = _profile_lines(uid) + _graph_lines(MAX_MEMORIES)
    return [{"content": s} for s in lines[:MAX_MEMORIES]]
