#!/usr/bin/env python3
"""Launch the CE arms of the dev pilot per repo once its collection is ready.

Ready = (bridge reported the upload indexed and the point count held for one 6-min poll, or the count held for
three polls) AND the dev freshness/routing gate passes.
Usage: pilot_when_ready.py REPO [...]   (env LIVELOC_KUBE_CONTEXT for digest stamping)
"""
import subprocess
import sys
import time

import llcore

POLL_S = 360


def points(coll):
    m = llcore.Mcp("/tmp")
    try:
        return m.tool("qdrant_status", {"collection": coll}).get("points_count")
    finally:
        m.close()


def main():
    pending = {llcore.tree_name(r, llcore.load_json(llcore.REPOS)[r]["snapshot"]): r for r in sys.argv[1:]}
    last, held = {}, {}
    while pending:
        colls = llcore.load_json(llcore.COLLECTIONS)
        for name, repo in list(pending.items()):
            coll = colls.get(name, {}).get("collection")
            if not coll:
                continue
            n = points(coll)
            held[name] = held.get(name, 0) + 1 if n and last.get(name) == n else 0
            last[name] = n
            done = colls[name].get("status") in ("indexed", "uploaded")
            print(time.strftime("%H:%M:%SZ", time.gmtime()), repo, "points", n, "held", held[name], "bridge_done", done,
                  flush=True)
            if not ((done and held[name] >= 1) or held[name] >= 3):
                continue
            subprocess.run([sys.executable, "freshness.py", "--split", "dev", name], check=False)
            if (llcore.load_json(llcore.COLLECTIONS)[name].get("fresh_split") or {}).get("dev"):
                print("READY", repo, flush=True)
                subprocess.run([sys.executable, "run.py", "--split", "dev", "--arms", "B,F,H,P", "--model", "haiku55",
                                "--jobs", "12", "--repos", repo], check=False)
                del pending[name]
        if pending:
            time.sleep(POLL_S)


if __name__ == "__main__":
    main()
