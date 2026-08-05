import os
import subprocess

import pytest

from routers import vera_memory_store as vm


def _git(*args, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=20, env=env)


@pytest.fixture
def worktree(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git("init", "-q", cwd=origin)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "root", cwd=origin)
    wt = tmp_path / "wt"
    _git("worktree", "add", "-q", str(wt), "-b", "feature", cwd=origin)
    return origin, wt


def _mirror_into(monkeypatch, target):
    monkeypatch.setattr(vm, "DIR", str(target))
    monkeypatch.setattr(vm, "DB_PATH", os.path.join(str(target), "store.db"))
    monkeypatch.setattr(vm, "MEMORY_MD", os.path.join(str(target), "MEMORY.md"))
    vm.init()
    vm.write("widget", "CORE belief about widgets", source="test", confidence=0.9, tier="core", kind="fact")
    return vm.mirror_markdown()


def test_mirror_never_commits_into_enclosing_worktree(monkeypatch, worktree):
    origin, wt = worktree
    head_before = _git("rev-parse", "HEAD", cwd=wt).stdout.strip()
    bare_before = _git("config", "core.bare", cwd=origin).stdout.strip()
    _mirror_into(monkeypatch, wt)
    assert os.path.exists(os.path.join(str(wt), "MEMORY.md"))
    assert _git("rev-parse", "HEAD", cwd=wt).stdout.strip() == head_before
    log = _git("log", "--format=%an %s", cwd=wt).stdout
    assert "Vera" not in log and "memory snapshot" not in log
    assert _git("config", "core.bare", cwd=origin).stdout.strip() == bare_before
    assert not os.path.isdir(os.path.join(str(wt), ".git"))


def test_mirror_never_commits_into_plain_directory(monkeypatch, tmp_path):
    target = tmp_path / "memdir"
    target.mkdir()
    _mirror_into(monkeypatch, target)
    assert os.path.exists(os.path.join(str(target), "MEMORY.md"))
    assert not os.path.exists(os.path.join(str(target), ".git"))


def test_mirror_still_snapshots_in_its_own_repo(monkeypatch, tmp_path):
    target = tmp_path / "memdir"
    target.mkdir()
    _git("init", "-q", cwd=target)
    assert _mirror_into(monkeypatch, target) is True
    log = _git("log", "--format=%an %s", cwd=target).stdout
    assert "Vera" in log and "memory snapshot" in log
