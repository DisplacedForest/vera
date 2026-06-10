"""Config-surface completeness guard — every env var vera-api reads must be discoverable
in the repo-root .env.example (active `NAME=` or documented `#NAME=` line), or carry an
explicit exemption here with a reason. Fails the moment a new os.environ/os.getenv read
lands without documentation, so the surface can never silently drift again.
Run: python3 -m pytest tests/test_env_surface.py
"""
import ast
import os
import pathlib
import re

API_ROOT = pathlib.Path(__file__).resolve().parent.parent          # services/vera-api
ENV_EXAMPLE = API_ROOT.parent.parent / ".env.example"              # repo root

# Vars deliberately NOT in .env.example — every entry needs a reason.
EXEMPT = {
    "VERA_VERSION",  # image-build fallback for /version when no VERSION file is baked; not deployment config
}


def _env_reads() -> dict[str, str]:
    """{var name: first file that reads it} for every constant-name env read in the API tree.
    Dynamically-built names (e.g. the scheduler's SCHEDULE_<JOB> pattern) aren't constants
    and are documented as a convention in .env.example instead."""
    out: dict[str, str] = {}
    for p in sorted(API_ROOT.rglob("*.py")):
        if "tests" in p.parts or "__pycache__" in p.parts:
            continue
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError:
            continue
        rel = str(p.relative_to(API_ROOT))
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                src = ast.unparse(node.func)
                if (src.endswith("environ.get") or src.endswith("os.getenv")) and node.args \
                        and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    name = node.args[0].value
            elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) \
                    and "environ" in ast.unparse(node.value) \
                    and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                name = node.slice.value
            if name:
                out.setdefault(name, rel)
    return out


def _documented() -> set[str]:
    text = ENV_EXAMPLE.read_text()
    return set(re.findall(r"^\s*#?([A-Z][A-Z0-9_]+)=", text, re.M))


def test_every_env_read_is_documented():
    reads = _env_reads()
    assert reads, "extractor found no env reads — it is broken, not the codebase"
    documented = _documented()
    undocumented = {name: where for name, where in sorted(reads.items())
                    if name not in documented and name not in EXEMPT}
    assert not undocumented, (
        "env vars read in code but missing from .env.example "
        "(add them there, or add an EXEMPT entry with a reason):\n"
        + "\n".join(f"  {n}  (first read in {w})" for n, w in undocumented.items()))


def test_exemptions_are_still_real():
    """An EXEMPT entry for a var nobody reads anymore is stale — prune it."""
    reads = _env_reads()
    stale = EXEMPT - set(reads)
    assert not stale, f"stale exemptions (no longer read anywhere): {sorted(stale)}"
