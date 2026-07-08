import os

import data_paths
import data_root
from tests import conftest


def test_env_set_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("VERA_DATA_DIR", str(tmp_path / "chosen"))
    assert data_root.resolve() == str(tmp_path / "chosen")
    assert os.path.isdir(tmp_path / "chosen")


def test_writable_data_dir_wins_next(monkeypatch, tmp_path):
    monkeypatch.delenv("VERA_DATA_DIR", raising=False)
    fake = tmp_path / "data"
    fake.mkdir()
    monkeypatch.setattr(data_root.os.path, "isdir", lambda p: p == "/data" or os.path.exists(p))
    real_access = os.access
    monkeypatch.setattr(data_root.os, "access", lambda p, m: True if p == "/data" else real_access(p, m))
    assert data_root.resolve() == "/data"


def test_home_fallback_creates_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("VERA_DATA_DIR", raising=False)
    real_isdir = os.path.isdir
    monkeypatch.setattr(data_root.os.path, "isdir", lambda p: False if p == "/data" else real_isdir(p))
    monkeypatch.setenv("HOME", str(tmp_path))
    root = data_root.resolve()
    assert root == str(tmp_path / ".vera" / "data")
    assert os.path.isdir(root)


def test_apply_setdefaults_every_store_path(monkeypatch, tmp_path):
    monkeypatch.setenv("VERA_DATA_DIR", str(tmp_path))
    for var in data_paths.STORE_PATHS:
        monkeypatch.delenv(var, raising=False)
    root = data_root.apply()
    assert root == str(tmp_path)
    for var, rel in data_paths.STORE_PATHS.items():
        assert os.environ[var] == os.path.join(str(tmp_path), rel)


def test_apply_never_overrides_explicit_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VERA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PULSE_DB_PATH", "/elsewhere/pulse.db")
    data_root.apply()
    assert os.environ["PULSE_DB_PATH"] == "/elsewhere/pulse.db"


def test_conftest_and_runtime_share_one_table():
    assert conftest.STORE_PATHS is data_paths.STORE_PATHS
