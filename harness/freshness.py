#!/usr/bin/env python3
"""Freshness + routing gate per collection, before any run.

Usage: freshness.py [--split dev|test] TREE_NAME [...]   (tree name = liveloc-<owner>__<repo>-<sha8>; default: all)

routing: a collection-less `search` sent through the proxy from the tree's cwd must answer from this collection.
fresh:   for every task gold file, a path-filtered `search` must return a row of that file whose code matches the
         snapshot tree at the claimed lines (verified_lines). Records collections.json[tree]["fresh_split"][split].
coverage: 40 seeded random uploaded source files must be searchable by path (>= 95%), so a half-built index
         that happens to contain the gold files does not pass.
"""
import hashlib
import json
import os
import sys

import llcore
import score
import treesit


def call(mcp, name, args, tries=4):
    """CE tool call that rides out transient 5xx from the edge (the gate must not fail on a blip)."""
    for i in range(tries):
        try:
            return mcp.tool(name, args)
        except RuntimeError:
            if i == tries - 1:
                raise
            llcore.time.sleep(15 * (i + 1))


def gold_fresh(mcp, collection, tree, f, defs):
    q = (defs[0][1] if defs else os.path.splitext(os.path.basename(f))[0])
    st = call(mcp, "search", {"query": q, "collection": collection, "path_glob": f, "limit": 3})
    view, rows, _ = score.ce_view(tree, json.dumps(st), collection)
    return bool(view.get(f)), rows[:3]


COVER_N, COVER_MIN = 40, 0.95


def coverage(mcp, collection, tree):
    import build_tasks
    import mine_tasks
    files = sorted(f for f in build_tasks.indexable_set(tree) if mine_tasks.is_src(f))
    files.sort(key=lambda f: hashlib.sha256(f.encode()).hexdigest())
    hit = 0
    for f in files[:COVER_N]:
        st = call(mcp, "search", {"query": os.path.splitext(os.path.basename(f))[0], "collection": collection,
                                 "path_glob": f, "limit": 1})
        hit += any(r.get("path") == f for r in st.get("results") or [])
    return hit / max(1, min(COVER_N, len(files)))


def main():
    args = sys.argv[1:]
    split = None
    if args[:1] == ["--split"]:
        split, args = args[1], args[2:]
    colls = llcore.load_json(llcore.COLLECTIONS)
    names = args or [k for k, v in colls.items() if v.get("collection")]
    tasks = llcore.load_jsonl(llcore.TASKS)
    for name in names:
        rec = colls[name]
        tree, coll = rec["tree"], rec["collection"]
        mcp = llcore.Mcp(tree)
        try:
            st = call(mcp, "search", {"query": "main", "limit": 1})
            routed = st.get("collection") == coll
            cov = coverage(mcp, coll, tree)
            bad, n = [], 0
            for t in tasks:
                if t["tree"] != name or (split and t["split"] != split):
                    continue
                for f in t["gold_files"]:
                    n += 1
                    ok, rows = gold_fresh(mcp, coll, tree, f, t["gold_defs"].get(f, []))
                    if not ok:
                        bad.append({"task": t["id"], "file": f, "rows": rows})
        finally:
            mcp.close()
        colls = llcore.load_json(llcore.COLLECTIONS)
        fs = colls[name].setdefault("fresh_split", {})
        for sp in ([split] if split else ["dev", "test"]):
            fs[sp] = routed and not bad and cov >= COVER_MIN
        colls[name].update({"routed": routed, "coverage": cov, "gold_checked": n, "gold_stale": bad, "fresh_checked_at": llcore.time.strftime("%Y-%m-%dT%H:%M:%SZ", llcore.time.gmtime())})
        llcore.save_json(llcore.COLLECTIONS, colls)
        print(name, split or "all", "routed", routed, f"coverage {cov:.2f}", "gold", n, "stale", len(bad), [b["file"] for b in bad][:5], flush=True)


if __name__ == "__main__":
    main()
