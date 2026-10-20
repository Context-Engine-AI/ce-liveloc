#!/usr/bin/env python3
"""Run headless Claude Code on CE-LiveLoc tasks across arms; score each run from its trace.

Usage: run.py --split dev|test [--arms A,B,F,H] [--model haiku55] [--jobs 12] [--ids a,b] [--repos o/r,...]
              [--cap USD] [--seed 1]

Arms (same task prompt for all; only tools / instructions differ):
  A  built-in tools only (Read, Grep, Glob, Bash; network/git/edit commands denied)
  B  A + Context Engine MCP tools via the proxy; collection routed by X-Collection from the cwd mapping
  F  B + one customer-style CLAUDE.md instruction (CE first), passed as an appended system prompt
  H  A + PreToolUse hook on Grep/Glob/Bash(grep|rg) that injects CE search hits (ce_hook.cjs)
Each run: cwd = the read-only snapshot tree, --max-turns 15, stream-json captured with timestamps.
"""
import argparse
import concurrent.futures as cf
import glob
import json
import os
import subprocess
import sys
import threading
import time

import llcore
import score

MODELS = {"haiku55": "us.anthropic.claude-haiku-5-5", "sonnet55": "us.anthropic.claude-sonnet-5-5"}
BUILTIN = ["Read", "Grep", "Glob", "Bash"]
DENY = ["Bash(curl:*)", "Bash(wget:*)", "Bash(gh:*)", "Bash(git:*)", "Bash(rm:*)", "Bash(mv:*)", "Bash(cp:*)",
        "Bash(python:*)", "Bash(python3:*)", "Bash(node:*)", "WebFetch", "WebSearch", "Edit", "Write", "NotebookEdit"]
CE_TOOLS = ["mcp__context-engine__search", "mcp__context-engine__repo_search", "mcp__context-engine__symbol_graph",
            "mcp__context-engine__context_answer", "mcp__context-engine__graph_query"]
CE_DENY = ["mcp__context-engine__qdrant_list", "mcp__context-engine__qdrant_status",
           "mcp__context-engine__set_session_defaults", "mcp__context-engine__cross_repo_search"]
MAX_TURNS = 15
BUDGET_USD = 1.0
TIMEOUT_S = 600
VALID_CAPS = ("error_max_turns", "error_max_budget_usd")
HOOK = os.path.join(llcore.HERE, "ce_hook.cjs")
HOOK_SETTINGS = os.path.join(llcore.WORK, "hook-settings.json")

PROMPT = """{title}

{body}

---
Find the code that must change to fix this. Answer with file paths and line ranges.
Do not modify any files. End your reply with one final line of the form:
ANSWER: {{"files": ["relative/path/to/file", ...], "spans": [{{"file": "relative/path/to/file", "start": 10, "end": 42}}]}}"""

CE_FIRST = ("Use the Context Engine tools (repo_search / search / symbol_graph) to locate code before "
            "Grep/Glob/Read; use Grep only for exact-string checks.")


def prompt_for(task):
    return PROMPT.format(title=task["issue_title"].strip(), body=task["issue_body"].strip())


def write_hook_settings():
    cfg = {"hooks": {"PreToolUse": [{"matcher": "Grep|Glob|Bash",
                                     "hooks": [{"type": "command", "command": f"node {HOOK}", "timeout": 5}]}]}}
    with open(HOOK_SETTINGS, "w") as fh:
        json.dump(cfg, fh)


def build_cmd(task, arm, model):
    ce = arm in ("B", "F")
    cmd = ["claude", "-p", prompt_for(task), "--setting-sources", "", "--strict-mcp-config",
           "--mcp-config", llcore.MCP_CFG if ce else llcore.NO_MCP_CFG,
           "--tools", ",".join(BUILTIN), "--allowedTools", " ".join(BUILTIN + (CE_TOOLS if ce else [])),
           "--disallowedTools", " ".join(DENY + (CE_DENY if ce else [])),
           "--model", MODELS[model], "--max-turns", str(MAX_TURNS), "--output-format", "stream-json", "--verbose",
           "--max-budget-usd", str(BUDGET_USD), "--no-session-persistence"]
    if arm == "F":
        cmd += ["--append-system-prompt", CE_FIRST]
    if arm == "H":
        cmd += ["--settings", HOOK_SETTINGS]
    return cmd


def run_key(tid, arm, model, seed):
    return f"{tid}__{arm}__{model}__s{seed}"


def run_one(task, arm, model, seed, collection, build):
    os.makedirs(llcore.RUNS, exist_ok=True)
    base = os.path.join(llcore.RUNS, run_key(task["id"], arm, model, seed))
    if os.path.exists(base + ".json"):
        return json.load(open(base + ".json"))
    tree = llcore.tree_dir(task["repo"], task["snapshot"])
    for ext in (".jsonl", ".hook.jsonl", ".stderr"):
        if os.path.exists(base + ext):
            os.remove(base + ext)
    env = {**os.environ, **llcore.bedrock_env(), "LIVELOC_HOOK_LOG": base + ".hook.jsonl",
           "LIVELOC_COLLECTION": collection or "", "DISABLE_AUTOUPDATER": "1"}
    t0 = time.time()
    with open(base + ".jsonl", "w") as out, open(base + ".stderr", "w") as err:
        proc = subprocess.Popen(build_cmd(task, arm, model), cwd=tree, stdout=subprocess.PIPE, stderr=err,
                                stdin=subprocess.DEVNULL, text=True, bufsize=1, env=env)
        timer = threading.Timer(TIMEOUT_S, proc.kill)
        timer.start()
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                ev = {"type": "raw", "text": line[:2000]}
            out.write(json.dumps({"_t": round(time.time() - t0, 3), "ev": ev}) + "\n")
        proc.wait()
        timed_out = not timer.is_alive() and proc.returncode != 0
        timer.cancel()
    result, init, tools = None, {}, {}
    for line in open(base + ".jsonl"):
        ev = json.loads(line)["ev"]
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            init = {k: ev.get(k) for k in ("model", "claude_code_version", "tools", "mcp_servers", "cwd")}
        elif ev.get("type") == "assistant":
            for b in ev.get("message", {}).get("content", []):
                if b.get("type") == "tool_use":
                    tools[b["name"]] = tools.get(b["name"], 0) + 1
        elif ev.get("type") == "result":
            result = ev
    usage = (result or {}).get("usage") or {}
    rec = {"id": task["id"], "repo": task["repo"], "language": task["language"], "arm": arm, "model": model,
           "seed": seed, "split": task["split"], "collection": collection,
           # hitting the turn/budget cap is a legitimate outcome (no answer), not an invalid run
           "ok": bool(result) and (not result.get("is_error") or result.get("subtype") in VALID_CAPS),
           "subtype": (result or {}).get("subtype"),
           "timed_out": timed_out, "returncode": proc.returncode, "wall_s": round(time.time() - t0, 1),
           "cost_usd": (result or {}).get("total_cost_usd"), "num_turns": (result or {}).get("num_turns"),
           "usage": usage, "total_tokens": sum(usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens",
                                                                            "cache_read_input_tokens", "output_tokens")),
           "tool_calls_by_name": tools, "ce_calls": sum(v for k, v in tools.items() if k.startswith(score.CE_PREFIX)),
           "init": init, "build": build, "stderr_tail": open(base + ".stderr").read()[-800:] if not result else ""}
    rec["score"] = score.score(task, tree, base + ".jsonl", base + ".hook.jsonl", collection)
    llcore.save_json(base + ".json", rec)
    return rec


def spent():
    tot = 0.0
    for p in glob.glob(os.path.join(llcore.RUNS, "*.json")):
        try:
            tot += json.load(open(p)).get("cost_usd") or BUDGET_USD
        except (ValueError, OSError):
            tot += BUDGET_USD
    return tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test"], required=True)
    ap.add_argument("--arms", default="A,B,F")
    ap.add_argument("--model", default="haiku55", choices=sorted(MODELS))
    ap.add_argument("--jobs", type=int, default=12)
    ap.add_argument("--ids")
    ap.add_argument("--repos")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--cap", type=float, default=60.0, help="hard $ cap over everything in work/runs")
    a = ap.parse_args()
    tasks = [t for t in llcore.load_jsonl(llcore.TASKS) if t["split"] == a.split]
    if a.ids:
        tasks = [t for t in tasks if t["id"] in set(a.ids.split(","))]
    if a.repos:
        tasks = [t for t in tasks if t["repo"] in set(a.repos.split(","))]
    colls = llcore.load_json(llcore.COLLECTIONS)
    arms = a.arms.split(",")
    if set(arms) - {"A"}:  # arm A never touches CE, so only CE arms need a fresh, routed collection
        ready = {k for k, v in colls.items() if v.get("collection") and (v.get("fresh_split") or {}).get(a.split)}
        skipped = sorted({t["tree"] for t in tasks} - ready)
        if skipped:
            print("not ready (unindexed or failed the freshness gate), skipped:", skipped, flush=True)
        tasks = [t for t in tasks if t["tree"] in ready]
    builds = llcore.indexer_digests()
    if len(builds) != 1:
        sys.exit(f"mcp-indexer-http must run exactly one digest: {builds}")
    llcore.write_mcp_configs()
    write_hook_settings()
    # interleave arms per task so a mid-run CE change hits all arms alike
    jobs = [(t, arm) for t in tasks for arm in arms]
    print(f"{len(jobs)} runs ({len(tasks)} tasks x {arms}) model={a.model} build={builds[0][:19]} "
          f"spent=${spent():.2f} cap=${a.cap}", flush=True)
    lock = threading.Lock()

    def job(t, arm):
        with lock:
            if spent() + BUDGET_USD > a.cap:
                raise RuntimeError("cost cap reached")
        return run_one(t, arm, a.model, a.seed, colls.get(t["tree"], {}).get("collection"), builds[0])

    t0 = time.time()
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        futs = {ex.submit(job, t, arm): (t, arm) for t, arm in jobs}
        for f in cf.as_completed(futs):
            t, arm = futs[f]
            try:
                r = f.result()
                s = r["score"]
                print(f"{t['id']} {arm} ok={r['ok']} turns={r['num_turns']} cost={r['cost_usd']} wall={r['wall_s']} "
                      f"ce={r['ce_calls']} hook={s['hook_hits']}/{s['hook_calls']} found={s['found']} ttg={s['ttg']} "
                      f"rec={s['answer_recall']:.2f}", flush=True)
            except Exception as e:  # noqa: BLE001 - one run failing must not stop the batch
                print(f"EXC {t['id']} {arm}: {e!r}", flush=True)
    print(f"done in {time.time() - t0:.0f}s; spent=${spent():.2f}; build at end {llcore.indexer_digests()}", flush=True)


if __name__ == "__main__":
    main()
