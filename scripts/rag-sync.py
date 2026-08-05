#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field

REFERENCE_ROOT = os.environ.get("REFERENCE_ROOT", "/mnt/user/reference")

DOMAIN_COLLECTIONS = {
    "medical": "First Aid",
    "water": "Water",
    "food-preservation": "Food Preservation",
    "sanitation": "Sanitation",
    "food-production": "Food Production",
    "energy": "Energy",
    "shelter": "Shelter & Repair",
    "comms": "Comms",
    "navigation": "Navigation & Weather",
}

SKIP_NAMES = {"COVERAGE.md", "README.md", ".DS_Store"}

ACCEPTED_EXTENSIONS = ("txt", "md", "pdf", "docx", "html")


@dataclass
class Action:
    kind: str
    name: str
    reason: str = ""
    file_id: str = ""


@dataclass
class CollectionReport:
    collection: str
    created: bool = False
    uploaded: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    def render(self) -> str:
        head = (f"== {self.collection} "
                f"({'created' if self.created else 'exists'}) "
                f"+{len(self.uploaded)} uploaded "
                f"~{len(self.replaced)} replaced "
                f"-{len(self.deleted)} deleted "
                f"={len(self.skipped)} skipped "
                f"x{len(self.failed)} failed")
        lines = [head]
        for name in self.uploaded:
            lines.append(f"   + {name}")
        for name in self.replaced:
            lines.append(f"   ~ {name}")
        for name in self.deleted:
            lines.append(f"   - {name}")
        for name, reason in self.skipped:
            lines.append(f"   = {name} ({reason})")
        for name, reason in self.failed:
            lines.append(f"   x {name} ({reason})")
        return "\n".join(lines)


@dataclass
class Report:
    collections: list[CollectionReport] = field(default_factory=list)

    @property
    def failed(self) -> list[tuple[str, str]]:
        return [item for c in self.collections for item in c.failed]

    def render(self) -> str:
        lines = [c.render() for c in self.collections]
        lines.append("")
        lines.append(f"collections created: {sum(1 for c in self.collections if c.created)}")
        lines.append(f"files uploaded: {sum(len(c.uploaded) for c in self.collections)}")
        lines.append(f"files replaced: {sum(len(c.replaced) for c in self.collections)}")
        lines.append(f"files deleted: {sum(len(c.deleted) for c in self.collections)}")
        lines.append(f"files skipped: {sum(len(c.skipped) for c in self.collections)}")
        lines.append(f"files failed: {len(self.failed)}")
        return "\n".join(lines)


class DocumentsApi:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def _request(self, method: str, path: str, body: bytes | None = None,
                 content_type: str | None = None) -> dict:
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(self.base + path, data=body, headers=headers,
                                     method=method)
        with urllib.request.urlopen(req, timeout=600) as r:
            raw = r.read()
        return json.loads(raw) if raw else {}

    def status(self) -> dict:
        return self._request("GET", "/documents/status")

    def collections(self) -> list[dict]:
        return self._request("GET", "/documents/collections").get("collections", [])

    def create_collection(self, name: str, description: str) -> dict:
        payload = json.dumps({"name": name, "description": description}).encode("utf-8")
        return self._request("POST", "/documents/collections", payload, "application/json")

    def collection_files(self, cid: str) -> list[dict]:
        return self._request("GET", f"/documents/collections/{cid}/files").get("files", [])

    def upload(self, cid: str, name: str, data: bytes) -> dict:
        boundary = uuid.uuid4().hex
        safe = name.replace('"', "_")
        body = (f"--{boundary}\r\nContent-Disposition: form-data; "
                f'name="upload"; filename="{safe}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n").encode("utf-8")
        body += data + f"\r\n--{boundary}--\r\n".encode("utf-8")
        return self._request("POST", f"/documents/collections/{cid}/files", body,
                             f"multipart/form-data; boundary={boundary}")

    def delete_file(self, fid: str) -> dict:
        return self._request("DELETE", f"/documents/files/{fid}")

    def reindex_file(self, fid: str) -> dict:
        return self._request("POST", f"/documents/files/{fid}/reindex",
                             b"{}", "application/json")


def index_verdict(response: dict) -> tuple[bool, str]:
    index: dict = {}
    if isinstance(response, dict):
        nested = response.get("index")
        index = nested if isinstance(nested, dict) else response
    status = str(index.get("status") or "unknown")
    if status == "ready":
        return True, ""
    detail = str(index.get("error") or index.get("detail") or status)
    return False, f"indexing {status}: {detail}" if detail != status else f"indexing {status}"


def file_extension(name: str) -> str:
    _, _, ext = name.rpartition(".")
    return ext.lower() if "." in name else ""


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(domain_dir: str) -> list[dict]:
    out: list[dict] = []
    for name in sorted(os.listdir(domain_dir)):
        path = os.path.join(domain_dir, name)
        if not os.path.isfile(path) or name in SKIP_NAMES or name.startswith("."):
            continue
        out.append({"name": name, "path": path, "size": os.path.getsize(path),
                    "sha256": sha256_of(path)})
    return out


def plan(entries: list[dict], server_files: list[dict], supported: list[str],
         file_cap: int) -> list[Action]:
    by_name = {str(f.get("name")): f for f in server_files}
    on_disk = {str(e["name"]) for e in entries}
    actions: list[Action] = []
    for entry in entries:
        name = str(entry["name"])
        ext = file_extension(name)
        if ext not in supported:
            actions.append(Action("skip", name, f"format '{ext or 'none'}' not in "
                                                f"{', '.join(supported)}"))
            continue
        if file_cap and int(entry["size"]) > file_cap:
            actions.append(Action("skip", name, f"{entry['size']} bytes passes the "
                                                f"{file_cap} byte per-file cap"))
            continue
        server = by_name.get(name)
        if server is None:
            actions.append(Action("upload", name))
            continue
        if str(server.get("sha256") or "") != str(entry["sha256"]):
            actions.append(Action("replace", name, "content changed on disk",
                                  str(server.get("id") or "")))
            continue
        if str(server.get("state") or "") == "failed":
            actions.append(Action("reindex", name, "previous indexing failed",
                                  str(server.get("id") or "")))
            continue
        actions.append(Action("skip", name, "already present with identical content"))
    for name, server in by_name.items():
        if name not in on_disk:
            actions.append(Action("delete", name, "no longer on disk",
                                  str(server.get("id") or "")))
    return actions


def http_detail(error: Exception) -> str:
    if isinstance(error, urllib.error.HTTPError):
        detail = ""
        try:
            detail = str(json.loads(error.read()).get("detail", ""))
        except Exception:
            pass
        return f"HTTP {error.code} {detail}".strip()
    return str(error)


def sync_collection(api: DocumentsApi, collection_name: str, domain_dir: str,
                    targets: dict[str, dict], supported: list[str], file_cap: int,
                    dry_run: bool) -> CollectionReport:
    report = CollectionReport(collection=collection_name)
    if not os.path.isdir(domain_dir):
        report.failed.append((collection_name, f"{domain_dir} is not a directory"))
        return report
    try:
        entries = manifest(domain_dir)
    except OSError as e:
        report.failed.append((collection_name, f"reading {domain_dir}: {e}"))
        return report

    target = targets.get(collection_name)
    server_files: list[dict] = []
    if target is None:
        report.created = True
        if not dry_run:
            try:
                target = api.create_collection(
                    collection_name, f"{collection_name} reference corpus.")
            except Exception as e:
                report.failed.append((collection_name,
                                      f"creating collection: {http_detail(e)}"))
                return report
            targets[collection_name] = target
    else:
        try:
            server_files = api.collection_files(str(target["id"]))
        except Exception as e:
            report.failed.append((collection_name, f"listing files: {http_detail(e)}"))
            return report

    sources = {str(e["name"]): str(e["path"]) for e in entries}
    for action in plan(entries, server_files, supported, file_cap):
        label = f"{collection_name}/{action.name}"
        if action.kind == "skip":
            report.skipped.append((label, action.reason))
            continue
        if dry_run:
            if action.kind == "upload":
                report.uploaded.append(label)
            elif action.kind == "replace":
                report.replaced.append(label)
            elif action.kind == "delete":
                report.deleted.append(label)
            else:
                report.uploaded.append(f"{label} (reindexed)")
            continue
        try:
            if action.kind == "delete":
                api.delete_file(action.file_id)
                report.deleted.append(label)
                continue
            if action.kind == "reindex":
                ok, detail = index_verdict(api.reindex_file(action.file_id))
                if ok:
                    report.uploaded.append(f"{label} (reindexed)")
                else:
                    report.failed.append((label, detail))
                continue
            if action.kind == "replace":
                api.delete_file(action.file_id)
            with open(sources[action.name], "rb") as f:
                data = f.read()
            ok, detail = index_verdict(api.upload(str(target["id"]), action.name, data))
            if not ok:
                report.failed.append((label, detail))
            elif action.kind == "replace":
                report.replaced.append(label)
            else:
                report.uploaded.append(label)
        except Exception as e:
            report.failed.append((label, f"{action.kind}: {http_detail(e)}"))
    return report


def run(api: DocumentsApi, pairs: list[tuple[str, str]], dry_run: bool) -> Report:
    status = api.status()
    if not status.get("configured"):
        raise SystemExit("the vera-api embeddings integration is not configured; set "
                         "VERA_EMBED_URL (and model) on the vera-api service, then re-run")
    supported = [str(f) for f in status.get("supported_formats", [])] or list(ACCEPTED_EXTENSIONS)
    file_cap = int(status.get("file_cap", 0) or 0)
    targets = {str(c["name"]): c for c in api.collections()}

    report = Report()
    for domain_dir, collection_name in pairs:
        report.collections.append(
            sync_collection(api, collection_name, domain_dir, targets, supported,
                            file_cap, dry_run))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile the reference corpus into the vera-api document store. "
                    "Adds new files, replaces changed ones, removes files deleted from "
                    "disk, and reindexes files whose previous indexing failed. Running "
                    "again with no disk changes does nothing.")
    parser.add_argument("directory", nargs="?", help="one domain directory to sync")
    parser.add_argument("collection", nargs="?", help="collection name for that directory")
    parser.add_argument("--all", action="store_true",
                        help=f"sync every mapped domain under {REFERENCE_ROOT} "
                             "(env REFERENCE_ROOT)")
    parser.add_argument("--vera-api-base", default=os.environ.get("VERA_API_BASE", ""),
                        help="vera-api base URL (env VERA_API_BASE)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan without writing anything")
    args = parser.parse_args(argv)

    if not args.vera_api_base.strip():
        parser.error("missing the vera-api base URL: pass --vera-api-base or set VERA_API_BASE")
    if args.all:
        pairs = [(os.path.join(REFERENCE_ROOT, domain), collection)
                 for domain, collection in DOMAIN_COLLECTIONS.items()]
    elif args.directory and args.collection:
        pairs = [(args.directory, args.collection)]
    else:
        parser.error("pass --all, or a domain directory and a collection name")

    report = run(DocumentsApi(args.vera_api_base), pairs, args.dry_run)
    print("dry run: nothing was written" if args.dry_run else "sync complete")
    print(report.render())
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
