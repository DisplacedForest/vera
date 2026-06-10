#!/usr/bin/env python3
"""vera-sync-capabilities — regenerate Vera's tool-selection catalog from the registry.

Reads services/vera-capabilities.json, builds a "pick the right capability" catalog, and
upserts it into Vera's OWUI system prompt between markers (idempotent — replaces, never
piles up). This is the selection-harness half of Vera-as-router: the frontier "trick" is
partly tool-use training (can't replicate locally) and partly a good tool catalog (this).

Creds: ~/.vera/config.json (base, owui_email, owui_password, model).
"""
import json
import os
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REG = os.path.join(REPO, "services", "capabilities.json")
START, END = "<!-- VERA-CAPABILITIES -->", "<!-- /VERA-CAPABILITIES -->"

if not os.path.exists(REG):
    raise SystemExit(f"capability registry not found: {REG} — nothing to sync")

CFG = json.load(open(os.path.expanduser("~/.vera/config.json")))
B, E, P, MODEL = CFG["base"], CFG["owui_email"], CFG["owui_password"], CFG["model"]


def call(m, p, t=None, b=None):
    r = urllib.request.Request(B + p, data=(json.dumps(b).encode() if b is not None else None), method=m)
    r.add_header("Content-Type", "application/json")
    if t:
        r.add_header("Authorization", "Bearer " + t)
    with urllib.request.urlopen(r, timeout=30) as x:
        return json.loads(x.read())


def build_catalog() -> str:
    caps = json.load(open(REG))["capabilities"]
    lines = [START,
             "# Your capabilities — choose deliberately",
             "You can call the tools below. Pick the right one for the request; prefer a tool over guessing. "
             "If a request spans several, chain them. Capabilities on a worker node may be unavailable while that worker is offline.",
             ""]
    for c in caps:
        lines.append(f"- **{c['tool']}** ({c['node']}) — {c['summary']}")
        lines.append(f"  - Use when: {c['use_when']}")
        lines.append(f"  - Avoid when: {c['avoid_when']}")
    lines.append(END)
    return "\n".join(lines)


def main():
    token = call("POST", "/api/v1/auths/signin", b={"email": E, "password": P})["token"]
    rec = call("GET", f"/api/v1/models/model?id={MODEL}", token)
    params = rec.get("params") or {}
    system = params.get("system", "")
    catalog = build_catalog()

    if START in system and END in system:
        pre, rest = system.split(START, 1)
        _, post = rest.split(END, 1)
        system = pre.rstrip() + "\n\n" + catalog + post
        action = "replaced"
    else:
        system = system.rstrip() + "\n\n" + catalog + "\n"
        action = "added"
    params["system"] = system

    # access_grants must round-trip: OWUI 0.9.6's ModelForm declares it a bare list with a
    # None default, so omitting it makes the server re-validate None against list and 500.
    form = {"id": rec["id"], "base_model_id": rec.get("base_model_id"), "name": rec.get("name"),
            "meta": rec.get("meta") or {}, "params": params,
            "access_control": rec.get("access_control"),
            "access_grants": rec.get("access_grants") or [],
            "is_active": rec.get("is_active", True)}
    call("POST", f"/api/v1/models/model/update?id={MODEL}", token, form)
    print(f"capability catalog {action} in Vera's system prompt ({len(json.load(open(REG))['capabilities'])} capabilities).")


if __name__ == "__main__":
    main()
