import hashlib
import importlib.util
import json
import pathlib
import sys

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "migrate_owui_knowledge.py"
_spec = importlib.util.spec_from_file_location("migrate_owui_knowledge", _SCRIPT)
mig = importlib.util.module_from_spec(_spec)
sys.modules["migrate_owui_knowledge"] = mig
_spec.loader.exec_module(mig)

SUPPORTED = ["txt", "md", "pdf", "docx", "html"]
CAP = 1000


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeOwui:
    def __init__(self, bases, files, contents, broken_kb=None, broken_files=None):
        self.bases = bases
        self.files = files
        self.contents = contents
        self.broken_kb = broken_kb or set()
        self.broken_files = broken_files or set()

    def knowledge_bases(self):
        return self.bases

    def knowledge_files(self, kb_id):
        if kb_id in self.broken_kb:
            raise RuntimeError("kb listing exploded")
        return self.files.get(kb_id, [])

    def file_content(self, file_id):
        if file_id in self.broken_files:
            raise RuntimeError("download exploded")
        return self.contents[file_id]


class FakeVera:
    def __init__(self, configured=True, collections=None, fail_upload=None,
                 index_failures=None, reindex_results=None):
        self.configured = configured
        self.existing = collections or []
        self.created = []
        self.uploads = []
        self.reindexed = []
        self.fail_upload = fail_upload or set()
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

    def reindex_file(self, fid):
        self.reindexed.append(fid)
        return self.reindex_results.get(fid, {"status": "ready", "chunks": 2})


def owui_file(fid, name, size=10):
    return {"id": fid, "filename": name, "meta": {"name": name, "size": size}}


def test_decide_upload_and_skip_paths():
    existing = {"a.pdf": sha(b"old")}
    assert mig.decide("b.pdf", 10, sha(b"x"), existing, SUPPORTED, CAP).action == "upload"
    identical = mig.decide("a.pdf", 10, sha(b"old"), existing, SUPPORTED, CAP)
    assert identical.action == "skip"
    assert "identical" in identical.reason
    conflict = mig.decide("a.pdf", 10, sha(b"new"), existing, SUPPORTED, CAP)
    assert conflict.action == "skip"
    assert "different content" in conflict.reason
    undetermined = mig.decide("a.pdf", 0, None, existing, SUPPORTED, CAP)
    assert undetermined.action == "skip"
    assert "compared at run time" in undetermined.reason


def test_decide_rejects_unsupported_and_oversized():
    assert mig.decide("x.epub", 10, None, {}, SUPPORTED, CAP).action == "unsupported"
    assert mig.decide("noext", 10, None, {}, SUPPORTED, CAP).action == "unsupported"
    assert mig.decide("x.pdf", CAP + 1, sha(b"x"), {}, SUPPORTED, CAP).action == "unsupported"


def test_migrate_creates_collections_and_uploads():
    owui = FakeOwui(
        [{"id": "kb1", "name": "Water", "description": "wet docs"}],
        {"kb1": [owui_file("f1", "well.pdf"), owui_file("f2", "rain.md")]},
        {"f1": b"pdfdata", "f2": b"mddata"})
    vera = FakeVera()
    report = mig.migrate(owui, vera, dry_run=False)
    assert vera.created[0]["name"] == "Water"
    assert vera.created[0]["description"] == "wet docs"
    assert report.collections_created == ["Water"]
    assert sorted(report.uploaded) == ["Water/rain.md", "Water/well.pdf"]
    assert report.failed == []


def test_migrate_rerun_is_noop():
    owui = FakeOwui(
        [{"id": "kb1", "name": "Water", "description": ""}],
        {"kb1": [owui_file("f1", "well.pdf")]},
        {"f1": b"pdfdata"})
    vera = FakeVera()
    mig.migrate(owui, vera, dry_run=False)
    vera.existing = [{"id": "col-Water", "name": "Water",
                      "files": vera._files["col-Water"]}]
    report = mig.migrate(owui, vera, dry_run=False)
    assert report.uploaded == []
    assert report.collections_created == []
    assert report.skipped == [("Water/well.pdf", "already present with identical content")]
    assert len(vera.uploads) == 1
    assert vera.reindexed == []


def test_index_failure_reports_failed_and_rerun_reindexes():
    owui = FakeOwui(
        [{"id": "kb1", "name": "Water", "description": ""}],
        {"kb1": [owui_file("f1", "well.pdf")]},
        {"f1": b"pdfdata"})
    vera = FakeVera(index_failures={"well.pdf": {"status": "failed", "error": "no extractable text"}})
    report = mig.migrate(owui, vera, dry_run=False)
    assert report.uploaded == []
    assert ("Water/well.pdf", "indexing failed: no extractable text") in report.failed

    vera.existing = [{"id": "col-Water", "name": "Water",
                      "files": vera._files["col-Water"]}]
    rerun = mig.migrate(owui, vera, dry_run=False)
    assert vera.reindexed == ["f-well.pdf"]
    assert rerun.uploaded == ["Water/well.pdf (reindexed)"]
    assert rerun.failed == []


def test_migrate_isolates_failures_including_malformed_metadata():
    files = [owui_file("f1", "dead.pdf"), owui_file("f2", "ok.pdf"),
             owui_file("f3", "sad.pdf"),
             {"id": "f4", "filename": None, "meta": "not-a-dict"}]
    owui = FakeOwui(
        [{"id": "kb1", "name": "Broken", "description": ""},
         {"id": "kb2", "name": "Water", "description": ""}],
        {"kb2": files},
        {"f2": b"good", "f3": b"upload-me", "f4": b"who-knows"},
        broken_kb={"kb1"}, broken_files={"f1"})
    vera = FakeVera(fail_upload={"sad.pdf"})
    report = mig.migrate(owui, vera, dry_run=False)
    assert report.uploaded == ["Water/ok.pdf"]
    reasons = dict(report.failed)
    assert "Broken" in reasons
    assert "Water/dead.pdf" in reasons
    assert "Water/sad.pdf" in reasons
    assert any("f4" in label for label, _ in report.skipped + report.failed)


def test_dry_run_writes_nothing_and_labels_name_matches_honestly():
    owui = FakeOwui(
        [{"id": "kb1", "name": "Water", "description": ""}],
        {"kb1": [owui_file("f1", "well.pdf"), owui_file("f2", "img.epub"),
                 owui_file("f3", "seen.pdf")]},
        {})
    vera = FakeVera(collections=[{
        "id": "col-Water", "name": "Water",
        "files": [{"id": "f-seen", "name": "seen.pdf", "sha256": sha(b"old"), "state": "ready"}]}])
    report = mig.migrate(owui, vera, dry_run=True)
    assert report.collections_created == []
    assert report.uploaded == ["Water/well.pdf"]
    skipped = dict(report.skipped)
    assert "compared at run time" in skipped["Water/seen.pdf"]
    assert "format" in skipped["Water/img.epub"]
    assert vera.created == []
    assert vera.uploads == []


def test_unconfigured_embeddings_aborts():
    vera = FakeVera(configured=False)
    with pytest.raises(SystemExit):
        mig.migrate(FakeOwui([], {}, {}), vera, dry_run=True)


def test_owui_pagination_walks_pages():
    pages = {
        "/api/v1/knowledge/?page=1": {"items": [{"id": "a"}, {"id": "b"}]},
        "/api/v1/knowledge/?page=2": {"items": [{"id": "c"}]},
        "/api/v1/knowledge/?page=3": {"items": []},
    }
    api = mig.OwuiApi("http://example", "key")
    api._get = lambda path: json.dumps(pages[path]).encode("utf-8")
    assert [i["id"] for i in api.knowledge_bases()] == ["a", "b", "c"]


def test_owui_pagination_stops_when_page_param_is_ignored():
    same = {"items": [{"id": "a"}, {"id": "b"}]}
    api = mig.OwuiApi("http://example", "key")
    api._get = lambda path: json.dumps(same).encode("utf-8")
    assert [i["id"] for i in api.knowledge_bases()] == ["a", "b"]


def test_index_verdict_shapes():
    assert mig.index_verdict({"file": {}, "index": {"status": "ready"}}) == (True, "")
    ok, detail = mig.index_verdict({"status": "failed", "error": "boom"})
    assert not ok and "boom" in detail
    ok, detail = mig.index_verdict({"status": "unconfigured", "detail": "no endpoint"})
    assert not ok and "no endpoint" in detail
    ok, _ = mig.index_verdict({})
    assert not ok


def test_report_render_counts():
    report = mig.Report(collections_created=["A"], uploaded=["A/x.pdf"],
                        skipped=[("A/y.pdf", "already present with identical content")],
                        failed=[("A/z.pdf", "boom")])
    text = report.render()
    assert "collections created: 1" in text
    assert "files uploaded: 1" in text
    assert "files skipped: 1" in text
    assert "files failed: 1" in text
