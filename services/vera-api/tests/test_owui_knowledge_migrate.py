import hashlib
import importlib.util
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
    def __init__(self, configured=True, collections=None, fail_upload=None):
        self.configured = configured
        self.existing = collections or []
        self.created = []
        self.uploads = []
        self.fail_upload = fail_upload or set()
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
        self._files.setdefault(cid, []).append({"name": name, "sha256": sha(data)})
        self.uploads.append((cid, name))
        return {"file": {"name": name}}


def owui_file(fid, name, size=10):
    return {"id": fid, "filename": name, "meta": {"name": name, "size": size}}


def test_decide_upload_and_skip_paths():
    existing = {"a.pdf": sha(b"old")}
    assert mig.decide("b.pdf", 10, sha(b"x"), existing, SUPPORTED, CAP).action == "upload"
    assert mig.decide("a.pdf", 10, sha(b"old"), existing, SUPPORTED, CAP).action == "skip"
    conflict = mig.decide("a.pdf", 10, sha(b"new"), existing, SUPPORTED, CAP)
    assert conflict.action == "skip"
    assert "different content" in conflict.reason


def test_decide_rejects_unsupported_and_oversized():
    assert mig.decide("x.epub", 10, None, {}, SUPPORTED, CAP).action == "unsupported"
    assert mig.decide("noext", 10, None, {}, SUPPORTED, CAP).action == "unsupported"
    assert mig.decide("x.pdf", CAP + 1, None, {}, SUPPORTED, CAP).action == "unsupported"


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
    assert len(report.skipped) == 1
    assert len(vera.uploads) == 1


def test_migrate_isolates_failures():
    owui = FakeOwui(
        [{"id": "kb1", "name": "Broken", "description": ""},
         {"id": "kb2", "name": "Water", "description": ""}],
        {"kb2": [owui_file("f1", "dead.pdf"), owui_file("f2", "ok.pdf"),
                 owui_file("f3", "sad.pdf")]},
        {"f2": b"good", "f3": b"upload-me"},
        broken_kb={"kb1"}, broken_files={"f1"})
    vera = FakeVera(fail_upload={"sad.pdf"})
    report = mig.migrate(owui, vera, dry_run=False)
    assert report.uploaded == ["Water/ok.pdf"]
    reasons = dict(report.failed)
    assert "Broken" in reasons
    assert "Water/dead.pdf" in reasons
    assert "Water/sad.pdf" in reasons


def test_dry_run_writes_nothing():
    owui = FakeOwui(
        [{"id": "kb1", "name": "Water", "description": ""}],
        {"kb1": [owui_file("f1", "well.pdf"), owui_file("f2", "img.epub")]},
        {})
    vera = FakeVera()
    report = mig.migrate(owui, vera, dry_run=True)
    assert report.collections_created == ["Water"]
    assert report.uploaded == ["Water/well.pdf"]
    assert len(report.skipped) == 1
    assert vera.created == []
    assert vera.uploads == []


def test_unconfigured_embeddings_aborts():
    vera = FakeVera(configured=False)
    with pytest.raises(SystemExit):
        mig.migrate(FakeOwui([], {}, {}), vera, dry_run=True)


def test_report_render_counts():
    report = mig.Report(collections_created=["A"], uploaded=["A/x.pdf"],
                        skipped=[("A/y.pdf", "already present with identical content")],
                        failed=[("A/z.pdf", "boom")])
    text = report.render()
    assert "collections created: 1" in text
    assert "files uploaded: 1" in text
    assert "files skipped: 1" in text
    assert "files failed: 1" in text
