#!/usr/bin/env python3
"""Freshness + routing gate per collection, before any run.

Usage: freshness.py TREE_NAME [...]   (tree name = liveloc-<owner>__<repo>-<sha8>; default: all indexed)

routing: a collection-less `search` sent through the proxy from the tree's cwd must answer from this collection.
fresh:   for every task gold file, a path-filtered `search` must return a row of that file whose code matches the
         snapshot tree at the claimed lines (verified_lines). Marks collections.json[tree]["fresh"] = True/False.
"""
import json
import os
import sys

import llcore
import score
import treesit


def gold_fresh(mcp, collection, tree, f, defs):
    q = (defs[0][1] if defs else os.path.splitext(os.path.basename(f))[0])
    st = mcp.tool("search", {"query": q, "collection": collection, "path_glob": f, "limit": 3})
    view, rows, _ = score.ce_view(tree, json.dumps(st), collection)
    return bool(view.get(f)), rows[:3]


def main():
    colls = llcore.load_json(llcore.COLLECTIONS)
    names = sys.argv[1:] or [k for k, v in colls.items() if v.get("collection")]
    tasks = llcore.load_jsonl(llcore.TASKS)
    for name in names:
        rec = colls[name]
        tree, coll = rec["tree"], rec["collection"]
        mcp = llcore.Mcp(tree)
        try:
            st = mcp.tool("search", {"query": "main", "limit": 1})
            routed = st.get("collection") == coll
            bad, n = [], 0
            for t in tasks:
                if t["tree"] != name:
                    continue
                for f in t["gold_files"]:
                    n += 1
                    ok, rows = gold_fresh(mcp, coll, tree, f, t["gold_defs"].get(f, []))
                    if not ok:
                        bad.append({"task": t["id"], "file": f, "rows": rows})
        finally:
            mcp.close()
        colls = llcore.load_json(llcore.COLLECTIONS)
        colls[name].update({"routed": routed, "gold_checked": n, "gold_stale": bad,
                            "fresh": routed and not bad, "fresh_checked_at": llcore.time.strftime("%Y-%m-%dT%H:%M:%SZ", llcore.time.gmtime())})
        llcore.save_json(llcore.COLLECTIONS, colls)
        print(name, "routed", routed, "gold", n, "stale", len(bad), [b["file"] for b in bad][:5], flush=True)


if __name__ == "__main__":
    main()
