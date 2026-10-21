#!/usr/bin/env python3
"""Wait for the Gitea collection to pass the dev freshness gate, then run the ce arm.

Usage: wait_ce.py RUN_ARMS_ARGS...   (e.g. wait_ce.py --model sonnet55 --arms ce --smoke)
Every 10 min: done if collections.json already records fresh_split.dev (pilot_when_ready.py may gate it too);
else one qdrant_status call, and harness/freshness.py --split dev runs only once the point count has held for a
poll (gated once per settled count) and nobody (e.g. pilot_when_ready.py) has gated it since the count settled,
so polling costs CE one status call per 10 min while indexing moves.
"""
import os
import subprocess
import sys
import time

import acore
from acore import llcore
from pilot_when_ready import points

POLL_S = 600


def fresh():
    return bool((llcore.load_json(llcore.COLLECTIONS)[acore.TREE_NAME].get("fresh_split") or {}).get("dev"))


def main():
    last, gated, since = None, None, None
    while not fresh():
        try:
            n = points(acore.collection())
        except Exception as e:  # noqa: BLE001 - a CE blip must not end the wait
            print(time.strftime("%H:%M:%SZ", time.gmtime()), "status failed:", repr(e)[:200], flush=True)
            n = None
        rec = llcore.load_json(llcore.COLLECTIONS)[acore.TREE_NAME]
        print(time.strftime("%H:%M:%SZ", time.gmtime()), "points", n, "last", last, "coverage", rec.get("coverage"),
              "checked", rec.get("fresh_checked_at"), flush=True)
        if n != last:
            since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if n and n == last and gated != n and (rec.get("fresh_checked_at") or "") < since:
            gated = n
            subprocess.run([sys.executable, "freshness.py", "--split", "dev", acore.TREE_NAME],
                           cwd=os.path.join(os.path.dirname(acore.HERE), "harness"))
            if fresh():
                break
        last = n
        time.sleep(POLL_S)
    print(time.strftime("%H:%M:%SZ", time.gmtime()), "GATE PASSED", acore.TREE_NAME, flush=True)
    while len(d := llcore.indexer_digests()) != 1:  # a CE rollout in progress: wait for one serving build
        print(time.strftime("%H:%M:%SZ", time.gmtime()), "digests", d, "- waiting for one build", flush=True)
        time.sleep(120)
    sys.exit(subprocess.run([sys.executable, "-u", "run_arms.py", *sys.argv[1:]], cwd=acore.HERE).returncode)


if __name__ == "__main__":
    main()
