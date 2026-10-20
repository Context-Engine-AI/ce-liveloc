#!/usr/bin/env python3
"""Fetch one snapshot tree per repo and index it into its own CE collection.

Usage:
  prepare.py fetch REPO [...]   download the snapshot tarball (codeload, no .git) into TREES, minus excluded dirs
  prepare.py index REPO [...]   upload with the bridge's normal `ctxce index`, settle, record work/collections.json
  prepare.py status             print the recorded collections

Snapshot SHAs and per-repo excludes (vendored / generated dirs only) come from data/repos.json.
Trees are plain folders: an agent cannot reach later commits or the fix through them.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

import llcore

COUNT_JS = os.path.join(llcore.HERE, "count_indexable.mjs")
BRIDGE_REPO = os.path.dirname(os.path.dirname(llcore.BRIDGE))
MAX_UPLOAD_MB = 900  # CE extraction limit is 1 GiB; keep headroom


def repos():
    return llcore.load_json(llcore.REPOS)


def indexable(tree, listing=False):
    out = subprocess.run(["node", COUNT_JS, BRIDGE_REPO, tree] + (["--list"] if listing else []),
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout) if listing else int(out.stdout.strip())


def fetch(repo):
    r = repos()[repo]
    tree = llcore.tree_dir(repo, r["snapshot"])
    if os.path.isdir(tree):
        return tree
    llcore.need_disk(f"fetch {repo}")
    tmp = tree + ".partial"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    excl = sum((["--exclude", f"*/{d.strip('/')}", "--exclude", f"*/{d.strip('/')}/*"] for d in r.get("exclude", [])), [])
    url = f"https://codeload.github.com/{repo}/tar.gz/{r['snapshot']}"
    cmd = f"curl -sSfL --retry 3 {url} | tar -xzf - -C {tmp} --strip-components 1 " + " ".join(f"'{x}'" for x in excl)
    subprocess.run(["bash", "-o", "pipefail", "-c", cmd], check=True)
    os.replace(tmp, tree)
    return tree


def tree_stats(tree):
    files = indexable(tree, listing=True)
    size = sum(os.path.getsize(os.path.join(tree, f)) for f in files)
    du = subprocess.run(["du", "-sm", tree], capture_output=True, text=True).stdout.split()[0]
    return {"indexable_files": len(files), "indexable_mb": round(size / 2**20, 1), "tree_mb": int(du)}


def settle(collection, poll_s=60, max_s=5400, stable_polls=4):
    """Wait until the collection's point count stops changing. Returns (points, seconds)."""
    mcp, last, stable, t0 = llcore.Mcp("/tmp"), None, 0, time.time()
    try:
        while time.time() - t0 < max_s:
            try:
                n = mcp.tool("qdrant_status", {"collection": collection}).get("points_count")
            except RuntimeError:
                n = None
            stable = stable + 1 if n and n == last else 0
            if stable >= stable_polls:
                return n, round(time.time() - t0)
            last = n if n else last
            time.sleep(poll_s)
        return last, None
    finally:
        mcp.close()


def index(repo):
    r = repos()[repo]
    name = llcore.tree_name(repo, r["snapshot"])
    colls = llcore.load_json(llcore.COLLECTIONS)
    if colls.get(name, {}).get("status") in ("indexed", "uploaded"):
        return colls[name]
    tree = fetch(repo)
    st = tree_stats(tree)
    if st["indexable_mb"] > MAX_UPLOAD_MB:
        raise SystemExit(f"{repo}: {st['indexable_mb']} MB of indexable files exceeds the {MAX_UPLOAD_MB} MB guard")
    os.makedirs(os.path.join(llcore.WORK, "logs"), exist_ok=True)
    t0 = time.time()
    with open(os.path.join(llcore.WORK, "logs", f"index-{name}.log"), "a") as err:
        out = subprocess.run(["node", llcore.BRIDGE, "index", tree, "--concurrency", "1", "--json", "--timeout", "5400"],
                             stdout=subprocess.PIPE, stderr=err, text=True)
    try:
        res = json.loads(out.stdout)["results"][0]
    except (ValueError, KeyError, IndexError):
        res = {"status": "failed", "error": f"unparsable bridge output (rc={out.returncode})"}
    rec = {"repo": repo, "snapshot": r["snapshot"], "tree": tree, "collection": res.get("collection"),
           "status": res.get("status"), "files": res.get("files"), "error": res.get("error"),
           "duration_s": round(time.time() - t0, 1), "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **st}
    if rec["collection"] and rec["status"] in ("uploaded", "timeout"):
        rec["settled_points"], rec["settle_s"] = settle(rec["collection"])
        if rec["settle_s"] is not None:
            rec["status"] = "uploaded"
    if rec["collection"]:
        llcore.map_workspace(tree, rec["collection"])
    colls = llcore.load_json(llcore.COLLECTIONS)
    colls[name] = rec
    llcore.save_json(llcore.COLLECTIONS, colls)
    return rec


def main():
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "fetch":
        for repo in args:
            tree = fetch(repo)
            print(repo, tree, json.dumps(tree_stats(tree)), f"free={llcore.free_gib():.1f}GiB", flush=True)
    elif cmd == "index":
        llcore.write_mcp_configs()
        for repo in args:
            rec = index(repo)
            print(repo, "->", rec.get("collection"), rec.get("status"), rec.get("files"), rec.get("duration_s"),
                  rec.get("error"), flush=True)
    elif cmd == "status":
        for k, v in llcore.load_json(llcore.COLLECTIONS).items():
            print(k, v.get("collection"), v.get("status"), v.get("files"), v.get("duration_s"), v.get("settled_points"))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
