#!/usr/bin/env python3
"""Fetch and index an extra audit repo into CE exactly like the LiveLoc repos (harness/prepare.py), without touching
the frozen LiveLoc task data: the repo entry is injected here instead of written to data/repos.json.

Usage: AUDIT_REPO=caldiy prepare_repo.py fetch|index
fetch: the snapshot tarball (codeload, no .git) of CFG["source"] into the tree named after CFG["name"].
index: the bridge's normal `ctxce index` upload, settle, and work/collections.json record (prepare.index).
"""
import os
import subprocess
import sys

import acore
from acore import llcore
import prepare  # harness/prepare.py


def fetch():
    tree = acore.TREE
    if os.path.isdir(tree):
        return tree
    llcore.need_disk(f"fetch {acore.CFG['source']}")
    tmp = tree + ".partial"
    subprocess.run(["rm", "-rf", tmp], check=True)
    os.makedirs(tmp)
    url = f"https://codeload.github.com/{acore.CFG['source']}/tar.gz/{acore.SNAPSHOT}"
    subprocess.run(["bash", "-o", "pipefail", "-c", f"curl -sSfL --retry 3 {url} | tar -xzf - -C {tmp} --strip-components 1"],
                   check=True)
    os.replace(tmp, tree)
    return tree


def main():
    if acore.AUDIT_REPO == "gitea":
        sys.exit("gitea is a LiveLoc repo: use harness/prepare.py")
    entry = {"snapshot": acore.SNAPSHOT, "language": acore.CFG["lang"], "exclude": []}
    base = prepare.repos
    prepare.repos = lambda: {**base(), acore.REPO: entry}  # inject; data/repos.json stays the frozen LiveLoc set
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "fetch":
        tree = fetch()
        print(acore.REPO, tree, prepare.tree_stats(tree), f"free={llcore.free_gib():.1f}GiB", flush=True)
    elif cmd == "index":
        fetch()
        llcore.write_mcp_configs()
        rec = prepare.index(acore.REPO)
        print(acore.REPO, "->", rec.get("collection"), rec.get("status"), rec.get("files"), rec.get("duration_s"),
              rec.get("settled_points"), rec.get("error"), flush=True)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
