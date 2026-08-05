import os

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
API_ROOT = os.path.join(REPO_ROOT, "services", "vera-api")
SCRIPTS_ROOT = os.path.join(REPO_ROOT, "scripts")

CHAT_BACKEND_ENV = ("OWUI_BASE", "OWUI_KEY")

MIGRATION_TOOL = "scripts/migrate_owui_knowledge.py"

THIRD_PARTY_API_V1 = {
    "scripts/migrate_owui_knowledge.py",
    "services/vera-api/routers/overseerr.py",
    "services/vera-api/routers/integrations_probes.py",
    "services/vera-api/routers/scout.py",
}


def _sources():
    for root, dirs, files in os.walk(API_ROOT):
        dirs[:] = [d for d in dirs if d not in ("tests", "__pycache__")]
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(root, name)
    for root, dirs, files in os.walk(SCRIPTS_ROOT):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if name.endswith(".py") or name.endswith(".sh"):
                yield os.path.join(root, name)


def _relative(path):
    return os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")


def test_sources_read_no_chat_backend_env():
    offenders = []
    for path in _sources():
        rel = _relative(path)
        if rel == MIGRATION_TOOL:
            continue
        source = open(path, encoding="utf-8").read()
        for name in CHAT_BACKEND_ENV:
            if name in source:
                offenders.append(f"{rel}: {name}")
    assert offenders == []


def test_sources_call_no_chat_backend_api_paths():
    offenders = [rel for rel in (_relative(p) for p in _sources())
                 if rel not in THIRD_PARTY_API_V1
                 and "/api/v1/" in open(os.path.join(REPO_ROOT, rel), encoding="utf-8").read()]
    assert offenders == []


def test_guard_covers_the_engine_and_the_scripts():
    covered = {_relative(p) for p in _sources()}
    assert "services/vera-api/routers/health.py" in covered
    assert "services/vera-api/main.py" in covered
    assert "scripts/rag-sync.py" in covered
    assert "scripts/vera-backup.sh" in covered
    assert MIGRATION_TOOL in covered
