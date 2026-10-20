#!/usr/bin/env python3
"""Freeze the CE-LiveLoc task set from mined candidates + the snapshot trees.

Usage: build_tasks.py [--per-repo 25] [--dev-frac 0.2]
  reads  work/candidates/<owner>__<repo>.jsonl for every repo in data/repos.json, trees under TREES
  writes data/tasks.jsonl, data/split-dev.txt, data/split-test.txt, data/build_report.json

Kept only if: one task per issue; every gold file is in the tree and uploaded by the bridge (indexable);
every hunk's old-side lines (context and deleted) equal the snapshot file's lines exactly.
Sampling and the dev/test split are deterministic sha256 orders (seed below), fixed before any run.
"""
import argparse
import collections
import hashlib
import json
import os
import subprocess

import llcore
import treesit

SEED = "ce-liveloc-20261020"
EXT_LANG = {".py": "Python", ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin", ".ts": "TypeScript",
            ".tsx": "TypeScript", ".js": "JavaScript", ".jsx": "JavaScript", ".vue": "Vue", ".c": "C", ".h": "C/C++",
            ".cpp": "C++", ".cc": "C++", ".hpp": "C++", ".cs": "C#", ".lua": "Lua", ".svelte": "Svelte"}


def h(*parts):
    return hashlib.sha256("|".join((SEED,) + parts).encode()).hexdigest()


def indexable_set(tree):
    js = os.path.join(llcore.HERE, "count_indexable.mjs")
    out = subprocess.run(["node", js, os.path.dirname(os.path.dirname(llcore.BRIDGE)), tree, "--list"],
                         capture_output=True, text=True, check=True)
    return set(json.loads(out.stdout))


def line_of(offs, b):
    lo, hi = 0, len(offs) - 1
    while lo < hi:  # last offset <= b
        mid = (lo + hi + 1) // 2
        if offs[mid] <= b:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1


def enclosing_def(tree, f, line):
    """(kind, name, start_line, end_line) of the smallest named definition overlapping `line`, or None."""
    p = os.path.join(tree, f)
    b = treesit.lines_to_bytes(p, line, line)
    offs, _ = treesit._line_offsets(p)
    if not b or offs is None:
        return None
    best = None
    for kind, name, ds, de in treesit.file_defs(p):
        if name and ds <= b[1] and de > b[0] and (best is None or de - ds < best[3] - best[2]):
            best = (kind, name, ds, de)
    if not best:
        return None
    return [best[0], best[1], line_of(offs, best[2]), line_of(offs, max(best[2], best[3] - 1))]


def validate(c, tree):
    """(changed_lines_by_file, why_rejected). Checks every hunk's old side against the snapshot file."""
    changed = {}
    for f in c["gold_files"]:
        lines = open(os.path.join(tree, f), encoding="utf-8", errors="replace").read().split("\n")
        n = len(lines)
        cl = set()
        for hk in c["gold_hunks"][f]:
            for o, text, kind in hk["lines"]:
                if kind in (" ", "-"):
                    if o < 1 or o > n or lines[o - 1].rstrip("\r") != text.rstrip("\r"):
                        return None, f"hunk mismatch {f}:{o}"
                    if kind == "-":
                        cl.add(o)
                else:  # insertion before old line o: anchor on its neighbours
                    cl.update(x for x in (o - 1, o) if 1 <= x <= n)
        if not cl:
            return None, f"no changed lines in {f}"
        changed[f] = treesit.merge([(x, x) for x in sorted(cl)])
    return changed, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-repo", type=int, default=25)
    ap.add_argument("--dev-frac", type=float, default=0.2)
    a = ap.parse_args()
    repos = llcore.load_json(llcore.REPOS)
    tasks, report = [], {}
    for repo, meta in repos.items():
        if meta.get("skip"):
            continue
        tree = llcore.tree_dir(repo, meta["snapshot"])
        cands = llcore.load_jsonl(os.path.join(llcore.WORK, "candidates", repo.replace("/", "__") + ".jsonl"))
        idx = indexable_set(tree)
        why, seen_issue, ok = collections.Counter(), set(), []
        for c in sorted(cands, key=lambda c: c["merged_at"]):
            if c["snapshot"] != meta["snapshot"]:
                why["other snapshot"] += 1; continue
            if c["issue"] in seen_issue:
                why["duplicate issue"] += 1; continue
            missing = [f for f in c["gold_files"] if f not in idx]
            if missing:
                why["gold file not uploaded by the bridge"] += 1; continue
            changed, bad = validate(c, tree)
            if bad:
                why["hunk mismatch vs snapshot"] += 1; continue
            seen_issue.add(c["issue"])
            fns = {f: [d for d in {tuple(x) for x in (enclosing_def(tree, f, ln) for s, e in iv for ln in range(s, e + 1)) if x}]
                   for f, iv in changed.items()}
            exts = collections.Counter(EXT_LANG.get(os.path.splitext(f)[1].lower(), "other") for f in c["gold_files"])
            ok.append({
                "id": c["id"], "repo": repo, "repo_language": meta["language"],
                "language": exts.most_common(1)[0][0], "snapshot": meta["snapshot"],
                "tree": llcore.tree_name(repo, meta["snapshot"]),
                "issue": c["issue"], "issue_url": c["issue_url"], "issue_created_at": c["issue_created_at"],
                "issue_title": c["issue_title"], "issue_body": c["issue_body"],
                "pr": c["pr"], "pr_url": c["pr_url"], "pr_title": c["pr_title"], "merged_at": c["merged_at"],
                "pr_base": c["pr_base"], "pre_merge": c["pre_merge"], "merge_commit": c["merge_commit"],
                "gold_files": c["gold_files"], "gold_blob": c["gold_blob"], "gold_changed": changed,
                "gold_defs": {f: sorted(map(list, v), key=lambda d: d[2]) for f, v in fns.items()},
                "n_gold": len(c["gold_files"]), "added_src": c["added_src"],
                "leak_path": c["leak_any_path"], "leak": c["leak"],
                "issue_predates_snapshot": c["issue_created_at"] < meta.get("snapshot_date", c["snapshot_date"]),
            })
        ok.sort(key=lambda t: h("sample", t["id"]))
        chosen = ok[:a.per_repo]
        chosen.sort(key=lambda t: h("split", t["id"]))
        n_dev = round(len(chosen) * a.dev_frac)
        for i, t in enumerate(chosen):
            t["split"] = "dev" if i < n_dev else "test"
        tasks += chosen
        report[repo] = {"candidates": len(cands), "valid": len(ok), "kept": len(chosen), "dev": n_dev,
                        "rejected": dict(why), "leak_path": sum(t["leak_path"] for t in chosen)}
        print(repo, report[repo], flush=True)
    tasks.sort(key=lambda t: (t["repo"], t["id"]))
    with open(llcore.TASKS, "w") as fh:
        for t in tasks:
            fh.write(json.dumps(t, sort_keys=True) + "\n")
    for split in ("dev", "test"):
        with open(os.path.join(llcore.DATA, f"split-{split}.txt"), "w") as fh:
            fh.write("".join(t["id"] + "\n" for t in tasks if t["split"] == split))
    test_blob = "".join(json.dumps(t, sort_keys=True) + "\n" for t in tasks if t["split"] == "test")
    report["_totals"] = {"tasks": len(tasks), "dev": sum(t["split"] == "dev" for t in tasks),
                         "test": sum(t["split"] == "test" for t in tasks),
                         "test_split_sha256": hashlib.sha256(test_blob.encode()).hexdigest(),
                         "by_language": dict(collections.Counter(t["language"] for t in tasks))}
    llcore.save_json(os.path.join(llcore.DATA, "build_report.json"), report)
    print(json.dumps(report["_totals"], indent=1))


if __name__ == "__main__":
    main()
