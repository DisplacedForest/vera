import hashlib
import importlib.util
import pathlib
import sys

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "rag-sync.py"
_spec = importlib.util.spec_from_file_location("rag_sync", _SCRIPT)
rag = importlib.util.module_from_spec(_spec)
sys.modules["rag_sync"] = rag
_spec.loader.exec_module(rag)

SUPPORTED = ["txt", "md", "pdf", "docx", "html"]
CAP = 1000


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeApi:
    def __init__(self, configured=True, collections=None, fail_upload=None,
                 fail_delete=None, index_failures=None, reindex_results=None):
        self.configured = configured
        self.existing = collections or []
        self.created = []
        self.uploads = []
        self.deleted = []
        self.reindexed = []
        self.fail_upload = fail_upload or set()
        self.fail_delete = fail_delete or set()
        self.index_failures = index_failures or {}
        self.reindex_results = reindex_results or {}
        self._files = {c["id"]: list(c.get("files", [])) for c in self.existing}

    def status(self):
        return {"configured": self.configured, "supported_formats": SUPPORTED,
                "file_cap": CAP}

    def collections(self):
        return [{"id": c["id"], "name": c["name"]} for c in self.existing]

    def create_collection(self, name, description):
        col = {"id": f"col-{name}", "name": name, "description": description}
        self.created.append(col)
        self._files[col["id"]] = []
        return col

    def collection_files(self, cid):
        return self._files.get(cid, [])

    def upload(self, cid, name, data):
        if name in self.fail_upload:
            raise RuntimeError("ingest exploded")
        index = self.index_failures.get(name, {"status": "ready", "chunks": 3})
        state = "ready" if index.get("status") == "ready" else "failed"
        self._files.setdefault(cid, []).append(
            {"id": f"f-{name}", "name": name, "sha256": sha(data), "state": state})
        self.uploads.append((cid, name))
        return {"file": {"name": name}, "index": index}

    def delete_file(self, fid):
        if fid in self.fail_delete:
            raise RuntimeError("delete exploded")
        self.deleted.append(fid)
        for cid, files in self._files.items():
            self._files[cid] = [f for f in files if f["id"] != fid]
        return {"ok": True}

    def reindex_file(self, fid):
        self.reindexed.append(fid)
        return self.reindex_results.get(fid, {"status": "ready", "chunks": 2})


def server_file(name, data=b"same", state="ready"):
    return {"id": f"f-{name}", "name": name, "sha256": sha(data), "state": state}


def write_corpus(tmp_path, files):
    domain = tmp_path / "water"
    domain.mkdir()
    for name, data in files.items():
        (domain / name).write_bytes(data)
    return str(domain)


def test_plan_uploads_absent_files():
    entries = [{"name": "well.pdf", "size": 10, "sha256": sha(b"new"), "path": "/x"}]
    actions = rag.plan(entries, [], SUPPORTED, CAP)
    assert [(a.kind, a.name) for a in actions] == [("upload", "well.pdf")]


def test_plan_replaces_on_sha_change_and_skips_matching():
    entries = [{"name": "well.pdf", "size": 10, "sha256": sha(b"new"), "path": "/x"},
               {"name": "rain.md", "size": 10, "sha256": sha(b"same"), "path": "/y"}]
    actions = {a.name: a for a in rag.plan(
        entries, [server_file("well.pdf"), server_file("rain.md")], SUPPORTED, CAP)}
    assert actions["well.pdf"].kind == "replace"
    assert actions["well.pdf"].file_id == "f-well.pdf"
    assert actions["rain.md"].kind == "skip"
    assert "identical" in actions["rain.md"].reason


def test_plan_deletes_files_gone_from_disk():
    actions = rag.plan([], [server_file("gone.pdf")], SUPPORTED, CAP)
    assert [(a.kind, a.name, a.file_id) for a in actions] == [
        ("delete", "gone.pdf", "f-gone.pdf")]
    assert actions[0].reason == "no longer on disk"


def test_plan_reindexes_failed_state_with_matching_sha():
    entries = [{"name": "well.pdf", "size": 10, "sha256": sha(b"same"), "path": "/x"}]
    actions = rag.plan(entries, [server_file("well.pdf", state="failed")], SUPPORTED, CAP)
    assert actions[0].kind == "reindex"
    assert actions[0].file_id == "f-well.pdf"


def test_plan_skips_unsupported_and_oversized():
    entries = [{"name": "book.epub", "size": 10, "sha256": sha(b"a"), "path": "/x"},
               {"name": "noext", "size": 10, "sha256": sha(b"b"), "path": "/y"},
               {"name": "huge.pdf", "size": CAP + 1, "sha256": sha(b"c"), "path": "/z"}]
    actions = {a.name: a for a in rag.plan(entries, [], SUPPORTED, CAP)}
    assert all(a.kind == "skip" for a in actions.values())
    assert "format 'epub'" in actions["book.epub"].reason
    assert "format 'none'" in actions["noext"].reason
    assert "per-file cap" in actions["huge.pdf"].reason


def test_plan_keeps_server_file_whose_disk_copy_is_unsupported():
    entries = [{"name": "book.epub", "size": 10, "sha256": sha(b"a"), "path": "/x"}]
    actions = rag.plan(entries, [server_file("book.epub")], SUPPORTED, CAP)
    assert [a.kind for a in actions] == ["skip"]


def test_run_creates_collection_and_uploads(tmp_path):
    domain = write_corpus(tmp_path, {"well.pdf": b"pdfdata", "rain.md": b"mddata"})
    api = FakeApi()
    report = rag.run(api, [(domain, "Water")], dry_run=False)
    assert api.created[0]["name"] == "Water"
    assert sorted(report.collections[0].uploaded) == ["Water/rain.md", "Water/well.pdf"]
    assert report.collections[0].created is True
    assert report.failed == []


def test_run_rerun_is_a_noop(tmp_path):
    domain = write_corpus(tmp_path, {"well.pdf": b"pdfdata"})
    api = FakeApi(collections=[{"id": "col-Water", "name": "Water",
                                "files": [server_file("well.pdf", b"pdfdata")]}])
    report = rag.run(api, [(domain, "Water")], dry_run=False)
    assert api.uploads == []
    assert api.deleted == []
    assert api.reindexed == []
    assert report.collections[0].skipped == [
        ("Water/well.pdf", "already present with identical content")]


def test_run_replaces_changed_file_by_deleting_first(tmp_path):
    domain = write_corpus(tmp_path, {"well.pdf": b"fresh"})
    api = FakeApi(collections=[{"id": "col-Water", "name": "Water",
                                "files": [server_file("well.pdf", b"stale")]}])
    report = rag.run(api, [(domain, "Water")], dry_run=False)
    assert api.deleted == ["f-well.pdf"]
    assert api.uploads == [("col-Water", "well.pdf")]
    assert report.collections[0].replaced == ["Water/well.pdf"]
    assert report.collections[0].uploaded == []


def test_run_deletes_server_file_removed_from_disk(tmp_path):
    domain = write_corpus(tmp_path, {"well.pdf": b"pdfdata"})
    api = FakeApi(collections=[{"id": "col-Water", "name": "Water",
                                "files": [server_file("well.pdf", b"pdfdata"),
                                          server_file("gone.pdf", b"whatever")]}])
    report = rag.run(api, [(domain, "Water")], dry_run=False)
    assert api.deleted == ["f-gone.pdf"]
    assert api.uploads == []
    assert report.collections[0].deleted == ["Water/gone.pdf"]


def test_run_reindexes_failed_file(tmp_path):
    domain = write_corpus(tmp_path, {"well.pdf": b"pdfdata"})
    api = FakeApi(collections=[{"id": "col-Water", "name": "Water",
                                "files": [server_file("well.pdf", b"pdfdata",
                                                      state="failed")]}])
    report = rag.run(api, [(domain, "Water")], dry_run=False)
    assert api.reindexed == ["f-well.pdf"]
    assert api.uploads == []
    assert report.collections[0].uploaded == ["Water/well.pdf (reindexed)"]

    api.reindex_results = {"f-well.pdf": {"status": "failed", "error": "no text layer"}}
    api.reindexed = []
    rerun = rag.run(api, [(domain, "Water")], dry_run=False)
    assert rerun.failed == [("Water/well.pdf", "indexing failed: no text layer")]


def test_run_isolates_per_file_failures(tmp_path):
    domain = write_corpus(tmp_path, {"ok.pdf": b"good", "sad.pdf": b"bad",
                                     "stuck.pdf": b"fresh"})
    api = FakeApi(collections=[{"id": "col-Water", "name": "Water",
                                "files": [server_file("stuck.pdf", b"stale")]}],
                  fail_upload={"sad.pdf"}, fail_delete={"f-stuck.pdf"})
    report = rag.run(api, [(domain, "Water")], dry_run=False)
    assert report.collections[0].uploaded == ["Water/ok.pdf"]
    reasons = dict(report.failed)
    assert "upload" in reasons["Water/sad.pdf"]
    assert "replace" in reasons["Water/stuck.pdf"]


def test_run_reports_index_failure_on_upload(tmp_path):
    domain = write_corpus(tmp_path, {"well.pdf": b"pdfdata"})
    api = FakeApi(index_failures={"well.pdf": {"status": "failed",
                                               "error": "no extractable text"}})
    report = rag.run(api, [(domain, "Water")], dry_run=False)
    assert report.collections[0].uploaded == []
    assert report.failed == [("Water/well.pdf", "indexing failed: no extractable text")]


def test_missing_directory_is_reported_not_raised(tmp_path):
    api = FakeApi()
    report = rag.run(api, [(str(tmp_path / "nope"), "Water")], dry_run=False)
    assert api.created == []
    assert "not a directory" in dict(report.failed)["Water"]


def test_dry_run_writes_nothing(tmp_path):
    domain = write_corpus(tmp_path, {"well.pdf": b"fresh", "new.md": b"newdata",
                                     "same.txt": b"same"})
    api = FakeApi(collections=[{"id": "col-Water", "name": "Water",
                                "files": [server_file("well.pdf", b"stale"),
                                          server_file("same.txt", b"same"),
                                          server_file("gone.pdf", b"whatever")]}])
    report = rag.run(api, [(domain, "Water")], dry_run=True)
    assert api.created == []
    assert api.uploads == []
    assert api.deleted == []
    assert api.reindexed == []
    collection = report.collections[0]
    assert collection.uploaded == ["Water/new.md"]
    assert collection.replaced == ["Water/well.pdf"]
    assert collection.deleted == ["Water/gone.pdf"]
    assert dict(collection.skipped)["Water/same.txt"]


def test_dry_run_plans_a_missing_collection_as_all_uploads(tmp_path):
    domain = write_corpus(tmp_path, {"well.pdf": b"pdfdata"})
    api = FakeApi()
    report = rag.run(api, [(domain, "Water")], dry_run=True)
    assert api.created == []
    assert report.collections[0].created is True
    assert report.collections[0].uploaded == ["Water/well.pdf"]


def test_housekeeping_files_are_never_ingested(tmp_path):
    domain = write_corpus(tmp_path, {"README.md": b"x", "COVERAGE.md": b"y",
                                     ".hidden.md": b"z", "real.md": b"r"})
    api = FakeApi()
    report = rag.run(api, [(domain, "Water")], dry_run=False)
    assert [name for _, name in api.uploads] == ["real.md"]
    assert report.collections[0].uploaded == ["Water/real.md"]


def test_unconfigured_embeddings_stops_before_any_write(tmp_path):
    domain = write_corpus(tmp_path, {"well.pdf": b"pdfdata"})
    api = FakeApi(configured=False)
    with pytest.raises(SystemExit):
        rag.run(api, [(domain, "Water")], dry_run=False)
    assert api.created == []
    assert api.uploads == []


def test_main_without_a_base_url_exits(monkeypatch):
    monkeypatch.delenv("VERA_API_BASE", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        rag.main(["--all"])
    assert excinfo.value.code != 0


def test_main_requires_a_target(monkeypatch):
    monkeypatch.setenv("VERA_API_BASE", "http://example")
    with pytest.raises(SystemExit) as excinfo:
        rag.main([])
    assert excinfo.value.code != 0


def test_domain_map_omits_security():
    assert "security" not in rag.DOMAIN_COLLECTIONS
    assert len(rag.DOMAIN_COLLECTIONS) == 9
    assert rag.DOMAIN_COLLECTIONS["medical"] == "First Aid"


def test_index_verdict_shapes():
    assert rag.index_verdict({"file": {}, "index": {"status": "ready"}}) == (True, "")
    ok, detail = rag.index_verdict({"status": "failed", "error": "boom"})
    assert not ok and "boom" in detail
    ok, _ = rag.index_verdict({})
    assert not ok


def test_report_render_counts(tmp_path):
    collection = rag.CollectionReport(collection="Water", created=True,
                                      uploaded=["Water/a.pdf"],
                                      replaced=["Water/b.pdf"],
                                      deleted=["Water/c.pdf"],
                                      skipped=[("Water/d.pdf", "identical")],
                                      failed=[("Water/e.pdf", "boom")])
    text = rag.Report(collections=[collection]).render()
    assert "collections created: 1" in text
    assert "files uploaded: 1" in text
    assert "files replaced: 1" in text
    assert "files deleted: 1" in text
    assert "files skipped: 1" in text
    assert "files failed: 1" in text
