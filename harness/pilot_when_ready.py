#!/usr/bin/env python3
"""Launch the CE arms of the dev pilot per repo once its collection is ready.

Ready = the point count held for one 6-min poll AND the dev freshness/routing/coverage gate passes
(freshness.py: dev gold files fresh, collection-less calls routed, >= 95% of 40 sampled source files indexed).
Usage: pilot_when_ready.py REPO [...]   (env LIVELOC_KUBE_CONTEXT for digest stamping)
"""
import os
import subprocess
import sys
import time

import llcore

POLL_S = 360
COORD = os.environ.get("LIVELOC_COORD_FILE")  # optional progress log to append a line to


def points(coll):
    m = llcore.Mcp("/tmp")
    try:
        return m.tool("qdrant_status", {"collection": coll}).get("points_count")
    finally:
        m.close()


def main():
    pending = {llcore.tree_name(r, llcore.load_json(llcore.REPOS)[r]["snapshot"]): r for r in sys.argv[1:]}
    last, held, gated = {}, {}, {}
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
            if held[name] < 1 or gated.get(name) == n:  # gate each settled point count once
                continue
            gated[name] = n
            if subprocess.run([sys.executable, "freshness.py", "--split", "dev", name]).returncode:
                gated.pop(name, None)  # the gate itself failed (e.g. CE 5xx): retry next poll
                continue
            if (llcore.load_json(llcore.COLLECTIONS)[name].get("fresh_split") or {}).get("dev"):
                print("READY", repo, flush=True)
                subprocess.run([sys.executable, "run.py", "--split", "dev", "--arms", "B,F,H,P", "--model", "haiku55",
                                "--jobs", "12", "--repos", repo], check=False)
                out = os.path.join(llcore.ROOT, "results", f"dev-haiku55-{repo.replace('/', '__')}.md")
                subprocess.run([sys.executable, "stats.py", "--split", "dev", "--model", "haiku55", "--repos", repo,
                                "--out", out], check=False)
                if COORD:
                    with open(COORD, "a") as fh:  # ping the coordinator; the table itself is in the results file
                        fh.write(f"- {time.strftime('%Y-%m-%d %H:%MZ', time.gmtime())} Claude (CE-LiveLoc): **pilot landed for "
                                 f"{repo}** (Haiku 5.5, dev, arms A/B/F/H/P). Table: {out}\n")
                del pending[name]
        if pending:
            time.sleep(POLL_S)


if __name__ == "__main__":
    main()
