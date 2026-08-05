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


@dataclass
class Decision:
    action: str
    reason: str = ""


@dataclass
class Report:
    collections_created: list[str] = field(default_factory=list)
    uploaded: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"collections created: {len(self.collections_created)}",
            f"files uploaded: {len(self.uploaded)}",
            f"files skipped: {len(self.skipped)}",
            f"files failed: {len(self.failed)}",
        ]
        for name in self.collections_created:
            lines.append(f"  created collection: {name}")
        for name in self.uploaded:
            lines.append(f"  uploaded: {name}")
        for name, reason in self.skipped:
            lines.append(f"  skipped: {name} ({reason})")
        for name, reason in self.failed:
            lines.append(f"  failed: {name} ({reason})")
        return "\n".join(lines)


def file_extension(name: str) -> str:
    _, _, ext = name.rpartition(".")
    return ext.lower() if "." in name else ""


def decide(name: str, size: int, sha256: str | None,
           existing: dict[str, str], supported_formats: list[str],
           file_cap: int) -> Decision:
    ext = file_extension(name)
    if ext not in supported_formats:
        return Decision("unsupported", f"format '{ext or 'none'}' not in "
                                       f"{', '.join(supported_formats)}")
    if file_cap and size > file_cap:
        return Decision("unsupported", f"{size} bytes passes the {file_cap} byte per-file cap")
    if name in existing:
        if sha256 is None:
            return Decision("skip", "name already present; content compared at run time")
        if existing[name] == sha256:
            return Decision("skip", "already present with identical content")
        return Decision("skip", "a file with this name exists with different content; "
                                "delete it in the Knowledge area to re-migrate")
    return Decision("upload")


class OwuiApi:
    def __init__(self, base: str, key: str):
        self.base = base.rstrip("/")
        self.key = key

    def _get(self, path: str) -> bytes:
        req = urllib.request.Request(self.base + path,
                                     headers={"Authorization": f"Bearer {self.key}"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()

    def _paged(self, path: str) -> list[dict]:
        sep = "&" if "?" in path else "?"
        out: list[dict] = []
        seen: set[str] = set()
        page = 1
        while True:
            data = json.loads(self._get(f"{path}{sep}page={page}"))
            items = data.get("items") if isinstance(data, dict) else data
            if not isinstance(items, list):
                raise ValueError("unexpected list shape from Open WebUI")
            fresh = [i for i in items
                     if isinstance(i, dict) and str(i.get("id")) not in seen]
            if not fresh:
                return out
            for i in fresh:
                seen.add(str(i.get("id")))
            out.extend(fresh)
            page += 1

    def knowledge_bases(self) -> list[dict]:
        return self._paged("/api/v1/knowledge/")

    def knowledge_files(self, kb_id: str) -> list[dict]:
        return self._paged(f"/api/v1/knowledge/{kb_id}/files")

    def file_content(self, file_id: str) -> bytes:
        return self._get(f"/api/v1/files/{file_id}/content")


class VeraApi:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def _request(self, method: str, path: str, body: bytes | None = None,
                 content_type: str | None = None) -> dict:
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(self.base + path, data=body, headers=headers,
                                     method=method)
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())

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


def migrate(owui: OwuiApi, vera: VeraApi, dry_run: bool) -> Report:
    report = Report()
    status = vera.status()
    if not status.get("configured"):
        raise SystemExit("the vera-api embeddings integration is not configured; set "
                         "VERA_EMBED_URL (and model) on the vera-api service, then re-run")
    supported = [str(f) for f in status.get("supported_formats", [])]
    file_cap = int(status.get("file_cap", 0) or 0)
    targets = {c["name"]: c for c in vera.collections()}

    for kb in owui.knowledge_bases():
        kb_name = str(kb.get("name") or "").strip()
        if not kb_name:
            report.failed.append(("(unnamed knowledge base)", "missing name"))
            continue
        try:
            files = owui.knowledge_files(str(kb["id"]))
        except Exception as e:
            report.failed.append((kb_name, f"listing files: {e}"))
            continue

        existing: dict[str, dict] = {}
        target = targets.get(kb_name)
        if target is None:
            if dry_run:
                report.collections_created.append(kb_name)
            else:
                try:
                    target = vera.create_collection(kb_name,
                                                    str(kb.get("description") or "").strip())
                except Exception as e:
                    report.failed.append((kb_name, f"creating collection: {e}"))
                    continue
                targets[kb_name] = target
                report.collections_created.append(kb_name)
        else:
            try:
                existing = {str(f["name"]): f for f in vera.collection_files(target["id"])}
            except Exception as e:
                report.failed.append((kb_name, f"listing target files: {e}"))
                continue
        existing_shas = {name: str(f.get("sha256") or "") for name, f in existing.items()}

        for f in files:
            try:
                meta = f.get("meta") if isinstance(f.get("meta"), dict) else {}
                name = str(f.get("filename") or meta.get("name") or f.get("id"))
                label = f"{kb_name}/{name}"
            except Exception as e:
                report.failed.append((f"{kb_name}/(unreadable file)", f"metadata: {e}"))
                continue
            try:
                verdict = decide(name, 0, None, existing_shas, supported, file_cap)
                if verdict.action == "unsupported":
                    report.skipped.append((label, verdict.reason))
                    continue
                if dry_run:
                    if verdict.action == "skip":
                        report.skipped.append((label, verdict.reason))
                    else:
                        report.uploaded.append(label)
                    continue
                try:
                    data = owui.file_content(str(f["id"]))
                except Exception as e:
                    report.failed.append((label, f"downloading: {e}"))
                    continue
                sha = hashlib.sha256(data).hexdigest()
                verdict = decide(name, len(data), sha, existing_shas, supported, file_cap)
                if verdict.action == "skip":
                    prior = existing.get(name) or {}
                    if (existing_shas.get(name) == sha
                            and str(prior.get("state")) == "failed" and prior.get("id")):
                        try:
                            response = vera.reindex_file(str(prior["id"]))
                        except Exception as e:
                            report.failed.append((label, f"reindex: {e}"))
                            continue
                        ok, detail = index_verdict(response)
                        if ok:
                            report.uploaded.append(f"{label} (reindexed)")
                        else:
                            report.failed.append((label, detail))
                    else:
                        report.skipped.append((label, verdict.reason))
                    continue
                if verdict.action != "upload":
                    report.skipped.append((label, verdict.reason))
                    continue
                try:
                    response = vera.upload(target["id"], name, data)
                except urllib.error.HTTPError as e:
                    detail = ""
                    try:
                        detail = json.loads(e.read()).get("detail", "")
                    except Exception:
                        pass
                    report.failed.append((label, f"upload: HTTP {e.code} {detail}".strip()))
                    continue
                except Exception as e:
                    report.failed.append((label, f"upload: {e}"))
                    continue
                ok, detail = index_verdict(response)
                if ok:
                    report.uploaded.append(label)
                else:
                    report.failed.append((label, detail))
            except Exception as e:
                report.failed.append((label, f"unexpected: {e}"))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate Open WebUI knowledge collections into the vera-api "
                    "document store. Files are re-chunked and re-embedded by vera-api's "
                    "configured embeddings integration; no vectors are copied and the "
                    "Open WebUI instance is never modified.")
    parser.add_argument("--owui-base", default=os.environ.get("OWUI_BASE", ""),
                        help="Open WebUI base URL (env OWUI_BASE)")
    parser.add_argument("--owui-key", default=os.environ.get("OWUI_API_KEY", ""),
                        help="Open WebUI API key (env OWUI_API_KEY)")
    parser.add_argument("--vera-api-base", default=os.environ.get("VERA_API_BASE", ""),
                        help="vera-api base URL (env VERA_API_BASE)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the migration plan without writing anything")
    args = parser.parse_args(argv)

    missing = [flag for flag, value in (("--owui-base", args.owui_base),
                                        ("--owui-key", args.owui_key),
                                        ("--vera-api-base", args.vera_api_base))
               if not value.strip()]
    if missing:
        parser.error("missing required settings: " + ", ".join(missing))

    report = migrate(OwuiApi(args.owui_base, args.owui_key),
                     VeraApi(args.vera_api_base), args.dry_run)
    header = "dry run: nothing was written" if args.dry_run else "migration complete"
    print(header)
    print(report.render())
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
