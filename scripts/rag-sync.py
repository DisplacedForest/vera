#!/usr/bin/env python3
"""rag-sync — reconcile the reference corpus into OWUI knowledge collections.

The standing RAG pipeline. Walks $REFERENCE_ROOT/<domain>/, and for each domain
reconciles its files into the matching OWUI collection using OWUI's incremental-sync
endpoints — add new, replace changed, remove deleted. Idempotent: re-running with no
disk changes does nothing. Drop a doc in a domain folder, run this (cron does it for
you), and it becomes queryable RAG.

A true reconcile, not a naive add-only ingest. Two facts
verified against live OWUI 0.9.6 shape it:
  - Built-in extraction handles PDFs server-side (no pdftotext needed) — we upload raw.
  - The upload accepts a client-supplied `file_hash` in `metadata`; /sync/diff compares
    the manifest checksum against that stored hash. So checksum == sha256(source file)
    makes the diff deterministic across runs.

Usage:
  rag-sync.py --all                         # reconcile every mapped domain
  rag-sync.py <domain-dir> "<Collection>"   # reconcile one domain into one collection
  rag-sync.py --dry-run --all               # show the plan, change nothing

Creds: an OWUI API key (env OWUI_KEY, used as a bearer token — what the cron uses, read
from /mnt/user/appdata/vera-api/.env) OR email+password (env OWUI_EMAIL/OWUI_PASSWORD or
~/.vera/config.json base/owui_email/owui_password). Reference root: env REFERENCE_ROOT
(the /mnt/user/reference default is an example-deployment value — an Unraid share).
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

REFERENCE_ROOT = os.environ.get("REFERENCE_ROOT", "/mnt/user/reference")

# domain folder -> OWUI collection display name. "medical" keeps the existing "First Aid"
# collection (the live pilot). "security" is intentionally omitted — curate it deliberately.
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

# files we never ingest (housekeeping, not reference content)
SKIP_NAMES = {"COVERAGE.md", "README.md", ".DS_Store"}

CONTENT_TYPES = {
    ".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/markdown",
    ".html": "text/html", ".htm": "text/html", ".epub": "application/epub+zip",
}


def _cfg():
    p = os.path.expanduser("~/.vera/config.json")
    c = json.load(open(p)) if os.path.exists(p) else {}
    return (os.environ.get("OWUI_BASE", c.get("base")),
            os.environ.get("OWUI_EMAIL", c.get("owui_email")),
            os.environ.get("OWUI_PASSWORD", c.get("owui_password")),
            os.environ.get("OWUI_KEY", c.get("owui_key")))


BASE, EMAIL, PW, KEY = _cfg()


def _req(method, path, token=None, json_body=None, multipart=None):
    url = BASE.rstrip("/") + path
    if multipart:
        boundary = "----verasync"
        fname, data, ctype, meta = multipart
        body = b""
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(fname)}"\r\n'.encode()
        body += f"Content-Type: {ctype}\r\n\r\n".encode() + data + b"\r\n"
        if meta is not None:
            body += f"--{boundary}\r\n".encode()
            body += b'Content-Disposition: form-data; name="metadata"\r\n\r\n'
            body += json.dumps(meta).encode() + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        r = urllib.request.Request(url, data=body, method=method)
        r.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    else:
        r = urllib.request.Request(url, data=(json.dumps(json_body).encode() if json_body is not None else None), method=method)
        r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=600) as x:
            return json.loads(x.read())
    except urllib.error.HTTPError as e:
        e.detail = e.read().decode()[:300]
        raise


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _collection(token, name, create=True):
    cols = _req("GET", "/api/v1/knowledge/", token)
    items = cols.get("items", cols) if isinstance(cols, dict) else cols
    col = next((c for c in items if c.get("name") == name), None)
    if col:
        return col["id"], False
    if not create:
        return None, False
    col = _req("POST", "/api/v1/knowledge/create", token,
               json_body={"name": name, "description": f"{name} reference for Vera (RAG, offline)."})
    return col["id"], True


def _upload(token, fpath, checksum):
    """Upload one file (raw; server extracts), stamping our checksum as file_hash.
    Polls until OWUI has extracted content, then returns the new file id."""
    ext = os.path.splitext(fpath)[1].lower()
    ctype = CONTENT_TYPES.get(ext, "text/plain")
    data = open(fpath, "rb").read()
    up = _req("POST", "/api/v1/files/", token,
              multipart=(os.path.basename(fpath), data, ctype, {"file_hash": checksum}))
    fid = up["id"]
    for _ in range(150):  # large/scanned PDFs extract slowly; wait up to ~5 min
        rec = _req("GET", f"/api/v1/files/{fid}", token)
        if (((rec or {}).get("data")) or {}).get("content"):
            return fid
        time.sleep(2)
    # No text layer (e.g. a scanned PDF) or extraction failed — drop the orphan upload.
    try:
        _req("DELETE", f"/api/v1/files/{fid}", token)
    except Exception:
        pass
    raise RuntimeError(f"no extracted content (no text layer?): {os.path.basename(fpath)}")


def _manifest(domain_dir):
    out = []
    for name in sorted(os.listdir(domain_dir)):
        fp = os.path.join(domain_dir, name)
        if not os.path.isfile(fp) or name in SKIP_NAMES or name.startswith("."):
            continue
        out.append({"path": "", "filename": name, "checksum": _sha256(fp),
                    "size": os.path.getsize(fp), "_fp": fp})
    return out


def sync_domain(token, domain_dir, collection_name, dry_run=False):
    if not os.path.isdir(domain_dir):
        print(f"  ! {domain_dir}: not a directory — skipping"); return
    manifest = _manifest(domain_dir)
    cid, created = _collection(token, collection_name, create=not dry_run)
    if cid is None:  # dry-run, collection doesn't exist yet — everything would be added
        print(f"\n== {collection_name}  (would create)  [{len(manifest)} file(s) on disk] ==")
        print(f"   plan: +{len(manifest)} add  ~0 update  -0 remove  =0 unchanged")
        for m in manifest:
            print(f"     + {m['filename']}")
        return
    tag = "created" if created else "exists"
    print(f"\n== {collection_name}  ({tag}, {cid[:8]})  [{len(manifest)} file(s) on disk] ==")

    diff = _req("POST", f"/api/v1/knowledge/{cid}/sync/diff", token,
                json_body={"manifest": [{"path": m["path"], "filename": m["filename"], "checksum": m["checksum"], "size": m["size"]} for m in manifest]})
    added = diff.get("added", []); modified = diff.get("modified", [])
    deleted = diff.get("deleted", []); unmod = diff.get("unmodified_count", 0)
    print(f"   plan: +{len(added)} add  ~{len(modified)} update  -{len(deleted)} remove  ={unmod} unchanged")
    if dry_run:
        for a in added: print(f"     + {a['filename']}")
        for m in modified: print(f"     ~ {m['filename']}")
        for d in deleted: print(f"     - {d['filename']}")
        return

    by_name = {m["filename"]: m for m in manifest}
    # Remove stale (changed) + deleted files FIRST. OWUI rejects adding a file whose
    # content duplicates one already in the collection, so a replacement must drop its
    # old version before the new one is added.
    stale = [mod["stale_file_id"] for mod in modified]
    cleanup_ids = stale + [d["file_id"] for d in deleted]
    if cleanup_ids or diff.get("rmdir"):
        _req("POST", f"/api/v1/knowledge/{cid}/sync/cleanup", token,
             json_body={"file_ids": cleanup_ids, "dir_ids": diff.get("rmdir", [])})
        for d in deleted: print(f"     - {d['filename']} -> removed")
        if stale: print(f"     (cleared {len(stale)} stale version(s) before re-embed)")
    # Then add new + replacement files. Each file is independent — one slow/scanned/
    # duplicate doc must not abort the rest of the run.
    for entry, mark in [(a, "+") for a in added] + [(mod, "~") for mod in modified]:
        m = by_name[entry["filename"]]
        try:
            fid = _upload(token, m["_fp"], m["checksum"])
            _req("POST", f"/api/v1/knowledge/{cid}/file/add", token, json_body={"file_id": fid})
            print(f"     {mark} {entry['filename']} -> embedded")
        except urllib.error.HTTPError as e:
            print(f"     x {entry['filename']} -> add failed {e.code}: {getattr(e,'detail','')}")
        except Exception as e:
            print(f"     x {entry['filename']} -> {str(e)[:90]}")


def main():
    args = [a for a in sys.argv[1:]]
    dry = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]
    if not (BASE and (KEY or (EMAIL and PW))):
        print("missing OWUI creds (env OWUI_KEY, or OWUI_EMAIL/OWUI_PASSWORD, or ~/.vera/config.json)"); sys.exit(1)
    token = KEY or _req("POST", "/api/v1/auths/signin", json_body={"email": EMAIL, "password": PW})["token"]

    if args and args[0] == "--all":
        for domain, coll in DOMAIN_COLLECTIONS.items():
            sync_domain(token, os.path.join(REFERENCE_ROOT, domain), coll, dry_run=dry)
    elif len(args) == 2:
        sync_domain(token, args[0], args[1], dry_run=dry)
    else:
        print(__doc__); sys.exit(1)
    print("\ndone.")


if __name__ == "__main__":
    main()
