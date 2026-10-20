#!/usr/bin/env python3
"""Probe candidate repos: snapshot commit before a date, indexable-ish file count, merged-PR volume after it."""
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

SNAP_UNTIL = os.environ.get("SNAP_UNTIL", "2026-09-01T00:00:00Z")
CODE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".rb", ".php", ".c", ".h", ".cpp", ".cc",
             ".hpp", ".cs", ".kt", ".swift", ".scala", ".sh", ".lua", ".sql", ".md", ".yml", ".yaml", ".toml",
             ".json", ".xml", ".html", ".css", ".scss", ".vue", ".svelte", ".dart", ".m", ".s"}
SRC_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".c", ".h", ".cpp", ".cc", ".hpp", ".kt"}
IGN = ("node_modules/", "vendor/", "third_party/", "thirdparty/", "dist/", "build/", "/node_modules/")


def gh(*args):
    r = subprocess.run(["gh", "api", *args], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[:200])
    return json.loads(r.stdout)


def probe(repo):
    try:
        info = gh(f"repos/{repo}")
        br = info["default_branch"]
        c = gh(f"repos/{repo}/commits?sha={br}&until={SNAP_UNTIL}&per_page=1")[0]
        sha = c["sha"]
        tree = gh(f"repos/{repo}/git/trees/{sha}?recursive=1")
        blobs = [t for t in tree["tree"] if t["type"] == "blob"]
        idx = [t for t in blobs if os.path.splitext(t["path"])[1].lower() in CODE_EXTS
               and not any(s in "/" + t["path"] for s in ["/" + i for i in IGN]) and 0 < t.get("size", 0) < 10 << 20]
        src = [t for t in idx if os.path.splitext(t["path"])[1].lower() in SRC_EXTS]
        q = f"repo:{repo} is:pr is:merged merged:>{SNAP_UNTIL[:10]}"
        n = gh("graphql", "-f", f"query=query{{search(query:\"{q}\",type:ISSUE,first:1){{issueCount}}}}")
        return {"repo": repo, "branch": br, "sha": sha, "date": c["commit"]["committer"]["date"],
                "truncated": tree.get("truncated"), "blobs": len(blobs), "indexable": len(idx), "src": len(src),
                "idx_mb": round(sum(t["size"] for t in idx) / 2**20), "all_mb": round(sum(t.get("size", 0) for t in blobs) / 2**20),
                "merged_prs": n["data"]["search"]["issueCount"]}
    except Exception as e:  # noqa: BLE001 - probe reports and moves on
        return {"repo": repo, "error": str(e)[:200]}


if __name__ == "__main__":
    with ThreadPoolExecutor(8) as ex:
        for r in ex.map(probe, sys.argv[1:]):
            print(json.dumps(r), flush=True)
