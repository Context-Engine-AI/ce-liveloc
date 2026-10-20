#!/usr/bin/env python3
"""Mine CE-LiveLoc candidate tasks from merged, issue-closing PRs (GitHub GraphQL + REST, read-only).

Usage: GH_TOKEN=... mine_tasks.py OWNER/REPO [...]   -> work/candidates/<owner>__<repo>.jsonl, one line per kept task

Per repo:
  snapshot = last default-branch commit before SNAP_UNTIL (one index per repo)
  PRs      = merged into the default branch after SNAP_UNTIL (and after 2026-07-01), closing >=1 same-repo issue
             whose body is a real description
  gold     = non-test source files the PR MODIFIED (1-4 of them)
  validity = every gold file's blob is identical at snapshot, PR baseRefOid and the merge commit's first parent,
             and every hunk's old-side lines equal the snapshot file's lines (checked later against the tree)
"""
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(os.path.dirname(HERE), "work")
CAND = os.path.join(WORK, "candidates")
SNAP_UNTIL = os.environ.get("SNAP_UNTIL", "2026-09-01T00:00:00Z")
MERGED_UNTIL = os.environ.get("MERGED_UNTIL", "2026-10-20")
MIN_BODY = 120
MAX_GOLD = 4

# gold must be code the bridge uploads (uploader.js CODE_EXTS) and that is "source", not config/docs
SRC_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".rb", ".php", ".c", ".h", ".cpp", ".cc",
            ".hpp", ".cs", ".kt", ".swift", ".scala", ".vue", ".svelte", ".lua", ".dart"}
TEST_RE = re.compile(r"(^|/)(tests?|__tests__|testing|testdata|test-data|fixtures?|e2e|spec|mocks?|benches|"
                     r"integration-tests?|testsuite|test_utils?)/|(^|/)test_[^/]*$|_test\.[a-z]+$|"
                     r"[._-](test|spec|tests|stories|bench)\.[a-z]+$|(Test|Tests|IT|TestCase)\.(java|kt)$|"
                     r"(^|/)conftest\.py$", re.I)
SKIP_DIR_RE = re.compile(r"(^|/)(vendor|third_party|thirdparty|node_modules|dist|build|generated|gen|docs?)/", re.I)


def gh(args, inp=None, tries=4):
    for i in range(tries):
        r = subprocess.run(["gh", "api", *args], capture_output=True, text=True, input=inp)
        if r.returncode == 0:
            return json.loads(r.stdout) if r.stdout.strip() else None
        if i == tries - 1:
            raise RuntimeError(r.stderr[:300])
        time.sleep(5 * (i + 1))


def gql(query, **vars_):
    args = ["graphql", "-f", f"query={query}"]
    for k, v in vars_.items():
        args += ["-F" if isinstance(v, int) else "-f", f"{k}={v}"]
    d = gh(args)
    if d.get("errors"):
        raise RuntimeError(str(d["errors"])[:300])
    return d["data"]


SEARCH = """query($q:String!,$after:String){search(query:$q,type:ISSUE,first:40,after:$after){issueCount
pageInfo{hasNextPage endCursor} nodes{... on PullRequest{number title url mergedAt baseRefName baseRefOid
mergeCommit{oid parents(first:2){nodes{oid}}}
closingIssuesReferences(first:5){nodes{number title body url createdAt repository{nameWithOwner}}}
files(first:100){totalCount nodes{path changeType additions deletions}}}}}}"""


def is_test(p):
    return bool(TEST_RE.search(p))


def is_src(p):
    return os.path.splitext(p)[1].lower() in SRC_EXTS and not is_test(p) and not SKIP_DIR_RE.search(p)


def clean_body(b):
    b = re.sub(r"<!--.*?-->", "", b or "", flags=re.S)
    b = re.sub(r"!\[[^\]]*\]\([^)]*\)", "[image]", b)
    b = re.sub(r"<img[^>]*>", "[image]", b, flags=re.I)
    b = re.sub(r"\n{3,}", "\n\n", b).strip()
    return b


def blob_oids(repo, pairs):
    """{(rev, path): oid or None} via batched GraphQL `object(expression:)`."""
    owner, name = repo.split("/")
    out, pairs = {}, list(dict.fromkeys(pairs))
    for i in range(0, len(pairs), 60):
        chunk = pairs[i:i + 60]
        body = " ".join(f'a{j}:object(expression:{json.dumps(r + ":" + p)}){{oid}}' for j, (r, p) in enumerate(chunk))
        d = gql(f'query{{repository(owner:"{owner}",name:"{name}"){{{body}}}}}')["repository"]
        for j, k in enumerate(chunk):
            out[k] = (d.get(f"a{j}") or {}).get("oid")
    return out


def snapshot(repo):
    info = gh([f"repos/{repo}"])
    br = info["default_branch"]
    c = gh([f"repos/{repo}/commits?sha={br}&until={SNAP_UNTIL}&per_page=1"])[0]
    return br, c["sha"], c["commit"]["committer"]["date"]


def parse_hunks(patch):
    """Old-side line ranges per hunk: [(old_start, old_len, [(old_line_no, text, kind)])]."""
    hunks, cur, o = [], None, 0
    for line in (patch or "").split("\n"):
        m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if m:
            cur = {"old_start": int(m.group(1)), "old_len": int(m.group(2) or 1), "lines": []}
            hunks.append(cur)
            o = cur["old_start"]
            continue
        if cur is None or line.startswith("\\"):
            continue
        if line.startswith("+"):
            cur["lines"].append([o, line[1:], "+"])  # inserted before old line o
        elif line.startswith("-"):
            cur["lines"].append([o, line[1:], "-"])
            o += 1
        else:
            cur["lines"].append([o, line[1:], " "])
            o += 1
    return hunks


def leak(issue_text, path):
    t = issue_text.lower()
    base = os.path.basename(path).lower()
    stem = os.path.splitext(base)[0]
    return {"path": path.lower() in t, "basename": base in t,
            "stem": len(stem) >= 5 and re.search(r"(?<![a-z0-9_])" + re.escape(stem) + r"(?![a-z0-9_])", t) is not None}


def reject(repo, pr, why):
    with open(os.path.join(CAND, repo.replace("/", "__") + ".rejected.jsonl"), "a") as fh:
        fh.write(json.dumps({"repo": repo, "pr": pr, "why": why}) + "\n")


def mine(repo):
    br, snap, snap_date = snapshot(repo)
    since = max(SNAP_UNTIL[:10], "2026-07-01")
    q = f"repo:{repo} is:pr is:merged base:{br} merged:{since}..{MERGED_UNTIL} linked:issue sort:created-asc"
    prs, after = [], None
    while True:
        d = gql(SEARCH, q=q, **({"after": after} if after else {}))["search"]
        prs += [n for n in d["nodes"] if n]
        if not d["pageInfo"]["hasNextPage"] or len(prs) >= 1000:
            break
        after = d["pageInfo"]["endCursor"]
    print(f"{repo}: snapshot {snap[:10]} ({snap_date}), {len(prs)} merged linked PRs", flush=True)
    cands = []
    for pr in prs:
        n = pr["number"]
        if not pr.get("mergeCommit") or pr["mergedAt"] <= SNAP_UNTIL:
            reject(repo, n, "not merged after snapshot"); continue
        issues = [i for i in pr["closingIssuesReferences"]["nodes"] if i["repository"]["nameWithOwner"].lower() == repo.lower()]
        issues = [dict(i, body=clean_body(i["body"])) for i in issues]
        issues = [i for i in issues if len(i["body"]) >= MIN_BODY]
        if not issues:
            reject(repo, n, "no same-repo closing issue with a real description"); continue
        files = pr["files"]["nodes"]
        if pr["files"]["totalCount"] > 100:
            reject(repo, n, "more than 100 files"); continue
        src = [f for f in files if is_src(f["path"])]
        gold = [f["path"] for f in src if f["changeType"] == "MODIFIED"]
        if not gold or len(gold) > MAX_GOLD:
            reject(repo, n, f"{len(gold)} modified source files"); continue
        cands.append({"pr": pr, "issues": issues, "gold": gold,
                      "added_src": [f["path"] for f in src if f["changeType"] != "MODIFIED"],
                      "other_files": [f["path"] for f in files if not is_src(f["path"]) and not is_test(f["path"])],
                      "test_files": [f["path"] for f in files if is_test(f["path"])]})
    pairs = []
    for c in cands:
        pre = c["pr"]["mergeCommit"]["parents"]["nodes"][0]["oid"]
        c["pre_merge"] = pre
        for p in c["gold"]:
            pairs += [(snap, p), (pre, p), (c["pr"]["baseRefOid"], p)]
    oids = blob_oids(repo, pairs)
    kept = 0
    for c in cands:
        pr = c["pr"]
        same = all(oids.get((snap, p)) and oids[(snap, p)] == oids.get((c["pre_merge"], p)) == oids.get((pr["baseRefOid"], p))
                   for p in c["gold"])
        if not same:
            reject(repo, pr["number"], "gold changed between snapshot and PR base"); continue
        pfiles = gh([f"repos/{repo}/pulls/{pr['number']}/files?per_page=100"])
        patches = {f["filename"]: f.get("patch") for f in pfiles}
        if any(not patches.get(p) for p in c["gold"]):
            reject(repo, pr["number"], "missing patch text for a gold file"); continue
        iss = c["issues"][0]
        text = f"{iss['title']}\n\n{iss['body']}"
        rec = {"id": f"{repo.replace('/', '__')}-{pr['number']}", "repo": repo, "snapshot": snap,
               "snapshot_date": snap_date, "pr": pr["number"], "pr_url": pr["url"], "pr_title": pr["title"],
               "merged_at": pr["mergedAt"], "pr_base": pr["baseRefOid"], "pre_merge": c["pre_merge"],
               "merge_commit": pr["mergeCommit"]["oid"], "issue": iss["number"], "issue_url": iss["url"],
               "issue_created_at": iss["createdAt"], "issue_title": iss["title"], "issue_body": iss["body"][:8000],
               "gold_files": c["gold"], "gold_blob": {p: oids[(snap, p)] for p in c["gold"]},
               "gold_hunks": {p: parse_hunks(patches[p]) for p in c["gold"]},
               "added_src": c["added_src"], "other_files": c["other_files"], "test_files": c["test_files"],
               "leak": {p: leak(text, p) for p in c["gold"]}}
        rec["leak_any_path"] = any(v["path"] or v["basename"] for v in rec["leak"].values())
        with open(os.path.join(CAND, repo.replace("/", "__") + ".jsonl"), "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        kept += 1
    print(f"{repo}: kept {kept} of {len(cands)} gold-shaped candidates", flush=True)


if __name__ == "__main__":
    os.makedirs(CAND, exist_ok=True)
    for r in sys.argv[1:]:
        try:
            mine(r)
        except Exception as e:  # noqa: BLE001 - one repo failing must not stop the others
            print(f"{r}: ERROR {e}", flush=True)
