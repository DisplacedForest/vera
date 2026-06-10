"""Media decisions store unit tests. Standalone — run: python3 tests/test_media_store.py"""
import os
import sys
import tempfile

os.environ["MEDIA_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "media.db")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routers"))
import media_store as ms  # noqa: E402


def test_record_and_seen():
    assert ms.seen("movie", 438631) is False
    ms.record("movie", 438631, "Dune", "skipped")
    assert ms.seen("movie", 438631) is True
    # different type, same id is a distinct key
    assert ms.seen("tv", 438631) is False


def test_seen_keys():
    ms.record("tv", 90228, "Dune: Prophecy", "approved")
    keys = ms.seen_keys()
    assert ("movie", 438631) in keys
    assert ("tv", 90228) in keys


def test_idempotent_latest_wins():
    ms.record("movie", 111, "X", "skipped")
    ms.record("movie", 111, "X", "approved")  # re-decide
    rows = [r for r in ms.all() if r["tmdb_id"] == 111]
    assert len(rows) == 1
    assert rows[0]["reason"] == "approved"


if __name__ == "__main__":
    test_record_and_seen()
    test_seen_keys()
    test_idempotent_latest_wins()
    print("OK")
