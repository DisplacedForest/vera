#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime


def _load(service_dir: str):
    sys.path.insert(0, service_dir)
    import importlib
    import pkgutil
    import routers
    for m in pkgutil.iter_modules(routers.__path__):
        try:
            importlib.import_module(f"routers.{m.name}")
        except Exception as e:
            print(f"skipped routers.{m.name}: {e}", file=sys.stderr)
    from routers import pulse_store, vein_engine
    return pulse_store, vein_engine


def _merged_definition(service_dir: str, draft_path: str) -> dict:
    with open(draft_path, encoding="utf-8") as f:
        draft = json.load(f)
    shipped_path = os.path.join(service_dir, "veins", f"{draft['kind']}.json")
    with open(shipped_path, encoding="utf-8") as f:
        defn = json.load(f)
    defn.pop("producer_jobs", None)
    defn["pipeline"] = draft["pipeline"]
    defn["schedule"] = draft["schedule"]
    return defn


def _live_snapshot(pulse_store, kind: str) -> list[dict]:
    return [{"id": c["id"], "title": c.get("title"), "situation_key": c.get("situation_key"),
             "category": c.get("category"), "change_set": c.get("change_set"),
             "severity": c.get("severity"), "status": c.get("status"), "day": c.get("day")}
            for c in pulse_store.list_cards()
            if c.get("kind") == kind and c.get("status") in ("new", "seen")]


def _dry_view(res: dict) -> dict:
    cards = [{"title": c.get("title"), "situation_key": c.get("situation_key"),
              "severity": c.get("severity"), "category": c.get("category"),
              "change_set": c.get("change_set")}
             for c in (res.get("cards") or [])]
    return {"ok": res.get("ok"), "situations": int(res.get("situations") or 0),
            "standing": res.get("standing", 0), "cards": cards,
            "steps": res.get("steps"), "detail": res.get("detail"),
            "block": res.get("block")}


def observe(args):
    pulse_store, vein_engine = _load(args.service_dir)
    defn = _merged_definition(args.service_dir, args.draft)
    from croniter import croniter
    os.makedirs(os.path.dirname(args.state) or ".", exist_ok=True)
    deadline = time.time() + args.window_hours * 3600
    it = croniter(defn["schedule"], datetime.now().astimezone())
    while True:
        if not args.once:
            nxt = it.get_next(datetime).timestamp()
            if nxt > deadline:
                break
            wait = max(0.0, nxt - time.time())
            print(f"next fire in {wait / 60:.1f}m", flush=True)
            time.sleep(wait)
        res = asyncio.run(vein_engine.run_definition(defn, dry_run=True, manual=True))
        row = {"ts": int(time.time()), "at": datetime.now().astimezone().isoformat(),
               "kind": defn["kind"], "dry": _dry_view(res),
               "live": _live_snapshot(pulse_store, defn["kind"])}
        with open(args.state, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        print(f"fire recorded: dry situations={row['dry']['situations']} "
              f"live cards={len(row['live'])}", flush=True)
        if args.once or time.time() >= deadline:
            break


def report(args):
    rows = []
    with open(args.state, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if not rows:
        print("no observations")
        return
    if args.live_categories:
        cats = {c.strip() for c in args.live_categories.split(",") if c.strip()}
        for r in rows:
            r["live"] = [c for c in r["live"] if (c.get("category") or "") in cats]
    kind = rows[0]["kind"]
    fires = len(rows)
    failures = [r for r in rows if not r["dry"]["ok"]]
    clean = [r for r in rows if r["dry"]["ok"]]
    quiet_both = sum(1 for r in clean if r["dry"]["situations"] == 0 and not r["live"])
    dry_quiet_live_not = sum(1 for r in clean if r["dry"]["situations"] == 0 and r["live"])
    live_quiet_dry_not = sum(1 for r in clean if r["dry"]["situations"] > 0 and not r["live"])
    dry_situations = {}
    for r in rows:
        for c in r["dry"]["cards"]:
            dry_situations.setdefault(c["situation_key"], c["title"])
    live_titles = {}
    for r in rows:
        for c in r["live"]:
            live_titles.setdefault(c["id"], (c["title"], c.get("category")))
    print(f"# Vein parity report: {kind}")
    print(f"window: {rows[0]['at']} to {rows[-1]['at']}, {fires} fires")
    print(f"quiet on both sides: {quiet_both}/{fires} fires")
    print(f"dry quiet while live had cards: {dry_quiet_live_not}")
    print(f"dry would post while live quiet: {live_quiet_dry_not}")
    if failures:
        print(f"DRY FAILURES: {len(failures)}")
        for r in failures[:5]:
            print(f"  {r['at']} block={r['dry'].get('block')} detail={r['dry'].get('detail')}")
    print()
    print(f"distinct dry situations ({len(dry_situations)}):")
    for k, t in sorted(dry_situations.items()):
        print(f"  {k}: {t}")
    print()
    print(f"distinct live cards over window ({len(live_titles)}):")
    for _id, (t, cat) in live_titles.items():
        print(f"  [{cat}] {t}")
    print()
    print("per-fire trace:")
    for r in rows:
        dk = ",".join(sorted(c["situation_key"] or "" for c in r["dry"]["cards"])) or "-"
        lk = ",".join(sorted(f"{c.get('category') or c.get('situation_key') or '?'}"
                             for c in r["live"])) or "-"
        standing = r["dry"].get("standing") or 0
        print(f"  {r['at']}  dry:{dk} standing:{standing}  live:{lk}")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="mode", required=True)
    ob = sub.add_parser("observe")
    ob.add_argument("--draft", required=True)
    ob.add_argument("--state", required=True)
    ob.add_argument("--service-dir", default="/app")
    ob.add_argument("--window-hours", type=float, default=24.0)
    ob.add_argument("--once", action="store_true")
    ob.set_defaults(fn=observe)
    rp = sub.add_parser("report")
    rp.add_argument("--state", required=True)
    rp.add_argument("--live-categories", default="")
    rp.set_defaults(fn=report)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
