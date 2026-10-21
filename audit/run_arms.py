#!/usr/bin/env python3
"""Run headless Claude Code on GrapeRoot's Gitea audit prompts in two arms with identical prompt text.

Usage: run_arms.py --model sonnet55|haiku55 [--arms normal,ce] [--smoke | --ids 1,4] [--repeats 3] [--jobs 6] [--cap 450]

Arms (prompt = GrapeRoot's prompt verbatim + acore.CITE_LINE; no system-prompt additions in either arm):
  normal  built-in Read/Grep/Glob/Bash only, no MCP servers
  ce      the same + the Context Engine MCP (stdio proxy; collection routed from the cwd mapping)
  ce-skill-*  ce + what the VS Code extension installs with mcpClaudeSkillEnabled (installSkillClaude): the skill folder
          skills/claude/context-engine/ copied verbatim to <config>/skills/context-engine/ and the line
          "# Important, always use skill `context-engine`" + blank line prepended to <config>/CLAUDE.md (empty here, as
          the other arms load no CLAUDE.md). <config> is a fresh per-run CLAUDE_CONFIG_DIR, loaded via
          --setting-sources user, so ~/.claude is never read or written; the Skill tool is added to its tools.
          ce-skill-shipped: the skill the extension ships (Context-Engine-Extension 187f674 = release 2.3.27).
          ce-skill-stale:   an older copy from the retired monorepo (Context-Engine 24d0ff49), run first by mistake.
          ce-skill-slim:    a 27-line candidate skill (audit/skills/slim), on its own frozen CE build.
          ce-skill-locate:  a 14-line skill making CE a locator (repo_search include_snippet=false, then Read), own build.
Both: --setting-sources "" (no CLAUDE.md/AGENTS.md, user hooks or plugins), --strict-mcp-config, cwd = the pinned
Gitea snapshot tree, harness/run.py's deny list (no network, git, edits, interpreters). Ceilings are identical safety
limits the agent is never told about: --max-turns 150, --max-budget-usd 8, 45 min wall; a run that hits one is a
valid (scored) outcome and the report counts hits. Set aside as .invalid-* and rerun by the next invocation (the report
counts attempts per arm): runs with no result or a transient API error (5xx, 429, overload, network), and ce runs whose
CE MCP server did not connect or whose CE build differs from the batch's pinned digest. Any other error is a valid,
scored failure. RUNS/_batch.json pins the CE build and the tree manifest across invocations.
"""
import re
import shutil
import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import subprocess
import threading
import time

import acore
import cites
from acore import llcore
import run as liveloc  # harness/run.py: BUILTIN, DENY, CE_TOOLS, CE_DENY

MAX_TURNS, MAX_BUDGET, TIMEOUT_S = 150, 8.0, 2700
SKILLS = {  # arm -> (git repo, commit, skill folder, SKILL.md sha256)
    "ce-skill-shipped": ("~/Desktop/pathful/Context-Engine-Extension", "187f674", "skills/claude/context-engine",
                         "f64e9db1980d82fc06ec2ded421b7b210e0f5ba9ec998e67b9484e0aba93388a"),
    "ce-skill-stale": ("~/Desktop/Context-Engine", "24d0ff49", "vscode-extension-saas/context-engine-uploader/skills/claude/context-engine",
                       "4375a2180bee737c0b9807437ce67764daee9b6385f05104f2914ad5700a0c06"),
    # commit None: a working-copy folder (not in git), pinned by its SKILL.md sha256
    "ce-skill-slim": ("~/Desktop/pathful/ce-liveloc", None, "audit/skills/slim",  # ran from work/skills/slim, same sha
                      "637e438c7beda87f7cd309e630afd1c4ae89c0fcdf98610c6244e2807672d4a4"),
    "ce-skill-locate": ("~/Desktop/pathful/ce-liveloc", None, "audit/skills/locate",
                        "d64429c1140558294dfe33cb4761f74cbd9e6d628787148fb89aee79a90f6ff3"),
}
ARMS = ("normal", "ce", *SKILLS)
CE_ARMS = ("ce", *SKILLS)
SKILL_RULE = "# Important, always use skill `context-engine`\n\n"  # mcp_config.js prependRulesFile
SKILL_HOMES = "/tmp/ce-audit/skill-home"
TRANSIENT = re.compile(r"(?i)\b(?:5\d\d|429|overloaded|rate.?limit|throttl\w*|timed? ?out|ECONN\w*|socket|network|"
                       r"unavailable|service.?error|internal.?server)\b")
RESERVE = {"sonnet55": 2.0, "haiku55": 0.3}  # $ held back per in-flight run when checking the cap


def build_cmd(prompt, arm, model):
    ce, skill = arm in CE_ARMS, arm in SKILLS
    tools = liveloc.BUILTIN + (["Skill"] if skill else [])
    return [acore.CLAUDE_BIN, "-p", prompt, "--setting-sources", "user" if skill else "", "--strict-mcp-config",
            "--mcp-config", llcore.MCP_CFG if ce else llcore.NO_MCP_CFG,
            "--tools", ",".join(tools),
            "--allowedTools", " ".join(tools + (liveloc.CE_TOOLS if ce else [])),
            "--disallowedTools", " ".join(liveloc.DENY + (liveloc.CE_DENY if ce else [])),
            "--model", acore.MODELS[model], "--max-turns", str(MAX_TURNS), "--max-budget-usd", str(MAX_BUDGET),
            "--output-format", "stream-json", "--verbose", "--no-session-persistence"]


def key(pid, arm, model, rep):
    return f"{pid:02d}__{arm}__{model}__r{rep}"


def summarize(events, model):
    results, init, tools, skills_used = [], {}, {}, []
    for ev in events:
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            init = {k: ev.get(k) for k in ("model", "claude_code_version", "tools", "mcp_servers", "plugins", "cwd", "skills", "slash_commands")}
        elif ev.get("type") == "assistant":
            for b in ev.get("message", {}).get("content", []):
                if b.get("type") == "tool_use":
                    tools[b["name"]] = tools.get(b["name"], 0) + 1
                    if b["name"] == "Skill":
                        skills_used.append(str((b.get("input") or {}).get("skill") or (b.get("input") or {}).get("command")))
        elif ev.get("type") == "result":
            results.append(ev)
    # a background Bash job finishing after the answer makes Claude Code emit another result segment: the answer is
    # every segment's text, turns and usage add up, total_cost_usd is already cumulative (take the last)
    res = dict(results[-1]) if results else {}
    if len(results) > 1:
        res["result"] = "\n\n".join(r.get("result") or "" for r in results)
        res["num_turns"] = sum(r.get("num_turns") or 0 for r in results)
        res["usage"] = {k: sum((r.get("usage") or {}).get(k) or 0 for r in results) for k in
                        ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")}
        res["is_error"] = any(r.get("is_error") for r in results)
    keys = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
    if res:
        usage = {k: (res.get("usage") or {}).get(k) or 0 for k in keys}
    else:  # killed before a result event: sum the last streamed usage of each call
        last = {e["message"].get("id"): e["message"].get("usage") or {} for e in events if e.get("type") == "assistant"}
        usage = {k: sum(u.get(k) or 0 for u in last.values()) for k in keys}
    cost, over = acore.run_cost(events, acore.MODELS[model])
    # num_turns as Claude Code counts it (tool round trips + 1) also when the run was killed before reporting it
    return {"subtype": res.get("subtype"), "is_error": res.get("is_error"), "result_segments": len(results),
            "num_turns": res.get("num_turns") if res else sum(tools.values()) + 1,
            "usage": usage, "total_tokens": sum(usage.values()), "cost_cc": res.get("total_cost_usd"), "cost_bedrock": cost,
            "haiku_calls_over_100k": over, "model_usage": res.get("modelUsage"), "tool_calls_by_name": tools,
            "tool_calls": sum(tools.values()), "ce_calls": sum(v for k, v in tools.items() if k.startswith("mcp__context-engine__")),
            "skill_calls": tools.get("Skill", 0), "skills_used": skills_used,
            "answer": res.get("result") or "", "init": init}


def skill_home(name, arm):
    """Fresh per-run config dir holding exactly what the extension installs into ~/.claude for the Claude skill,
    with the skill folder taken from git at the arm's pinned commit."""
    repo, commit, folder, sha = SKILLS[arm]
    repo = os.path.expanduser(repo)
    home = os.path.join(SKILL_HOMES, name)
    shutil.rmtree(home, ignore_errors=True)
    if commit is None:
        shutil.copytree(os.path.join(repo, folder), os.path.join(home, "skills", "context-engine"))
    else:
        files = subprocess.run(["git", "-C", repo, "ls-tree", "-r", "--name-only", commit, folder + "/"],
                               capture_output=True, text=True, check=True).stdout.split()
        for f in files:
            dest = os.path.join(home, "skills", "context-engine", os.path.relpath(f, folder))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as fh:
                fh.write(subprocess.run(["git", "-C", repo, "show", f"{commit}:{f}"], capture_output=True, check=True).stdout)
    got = hashlib.sha256(open(os.path.join(home, "skills", "context-engine", "SKILL.md"), "rb").read()).hexdigest()
    if got != sha:
        raise SystemExit(f"{arm} SKILL.md sha256 {got} != pinned {sha}")
    with open(os.path.join(home, "CLAUDE.md"), "w") as fh:
        fh.write(SKILL_RULE)
    return home


def skill_loaded(init):
    # a user skill is listed under slash_commands (init "skills" holds only the bundled ones)
    return "Skill" in (init.get("tools") or []) and "context-engine" in (init.get("skills") or []) + (init.get("slash_commands") or [])


def ce_connected(init):
    srv = [m for m in init.get("mcp_servers") or [] if m.get("name") == "context-engine"]
    return bool(srv) and srv[0].get("status") == "connected" and any(
        t.startswith("mcp__context-engine__") for t in init.get("tools") or [])


def run_one(item, arm, model, rep, coll, build):
    base = os.path.join(acore.RUNS, key(item["id"], arm, model, rep))
    if os.path.exists(base + ".json"):
        prev = json.load(open(base + ".json"))
        if prev.get("ok") and prev.get("digest_valid") is not False and \
                (prev.get("init") or {}).get("claude_code_version") in (acore.CLAUDE_VERSION, None):
            return prev
        stamp = time.strftime("%Y%m%dT%H%M%S")  # failed, build changed or wrong CLI version: keep it aside, run again
        for ext in (".json", ".jsonl", ".stderr"):
            if os.path.exists(base + ext):
                os.replace(base + ext, base + f".invalid-{stamp}{ext}")
    attempt = 1 + len([f for f in os.listdir(acore.RUNS) if f.startswith(os.path.basename(base) + ".invalid-")
                       and f.endswith(".json")])
    llcore.need_disk(f"audit run {os.path.basename(base)}", wait_s=4 * 3600)  # other drivers share this disk
    prompt = acore.prompt_text(item)
    env = {**os.environ, **llcore.bedrock_env(), "DISABLE_AUTOUPDATER": "1"}
    if arm in SKILLS:
        env["CLAUDE_CONFIG_DIR"] = skill_home(os.path.basename(base), arm)
    d0 = llcore.indexer_digests()
    t0 = time.time()
    events = []
    killed = threading.Event()
    with open(base + ".jsonl", "w") as out, open(base + ".stderr", "w") as err:
        proc = subprocess.Popen(build_cmd(prompt, arm, model), cwd=acore.TREE, stdout=subprocess.PIPE, stderr=err,
                                stdin=subprocess.DEVNULL, text=True, bufsize=1, env=env)
        timer = threading.Timer(TIMEOUT_S, lambda: (killed.set(), proc.kill()))
        timer.start()
        for line in proc.stdout:
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                ev = {"type": "raw", "text": line[:2000]}
            events.append(ev)
            out.write(json.dumps({"_t": round(time.time() - t0, 3), "ev": ev}) + "\n")
        proc.wait()
        timer.cancel()
    timed_out = killed.is_set()
    s = summarize(events, model)
    err_text = (s["answer"] + open(base + ".stderr").read()[-2000:]) if s["is_error"] or not s["subtype"] else ""
    transient = bool(TRANSIENT.search(err_text))
    d1 = llcore.indexer_digests()
    rec = {"id": item["id"], "category": item["category"], "task_type": item["task_type"], "arm": arm, "model": model,
           "rep": rep, "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()[:16], **s,
           # a run that ends at a safety ceiling (turns, budget or wall clock) is a legitimate (bad) outcome
           "ok": timed_out or (s["subtype"] is not None and not (s["is_error"] and transient)),
           "error_kind": None if not (s["is_error"] or not s["subtype"]) else "transient" if transient else "final",
           "attempt": attempt,
           "hit_ceiling": s["subtype"] in liveloc.VALID_CAPS or timed_out, "timed_out": timed_out,
           "returncode": proc.returncode, "wall_s": round(time.time() - t0, 1),
           "graperoot_sha": acore.GRAPEROOT_SHA, "snapshot": acore.SNAPSHOT, "collection": coll if arm in CE_ARMS else None,
           "digest_start": d0, "digest_end": d1, "stderr_tail": "" if s["subtype"] else open(base + ".stderr").read()[-800:]}
    if arm in CE_ARMS:  # one pinned CE build for the whole batch, and the MCP server must have connected
        rec["ce_connected"] = ce_connected(s["init"])
        rec["digest_valid"] = d0 == d1 == [build]
        rec["ok"] = rec["ok"] and rec["ce_connected"]
    if s["init"].get("claude_code_version") != acore.CLAUDE_VERSION:
        rec["ok"] = False
    if arm in SKILLS:  # the arm is invalid unless Claude Code listed the skill and offered the Skill tool
        repo, commit, folder, sha = SKILLS[arm]
        rec["skill_loaded"], rec["skill_sha256"] = skill_loaded(s["init"]), sha
        rec["skill_source"] = f"{os.path.basename(os.path.expanduser(repo))}@{commit or 'working-copy'}:{folder}"
        rec["ok"] = rec["ok"] and rec["skill_loaded"]
        shutil.rmtree(env["CLAUDE_CONFIG_DIR"], ignore_errors=True)
    c = cites.check(rec["answer"])
    rec["cites"] = {k: v for k, v in c.items() if k != "rows"}
    rec["cite_rows"] = c["rows"]
    llcore.save_json(base + ".json", rec)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, choices=sorted(acore.MODELS))
    ap.add_argument("--arms", default="normal,ce")
    ap.add_argument("--ids")
    ap.add_argument("--smoke", action="store_true", help="the 10 smoke prompts (acore.SMOKE_IDS)")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--cap", type=float, default=450.0, help="hard $ cap over all audit runs + Opus judgments")
    a = ap.parse_args()
    prompts = acore.load_prompts()
    ids = acore.SMOKE_IDS if a.smoke else ([int(x) for x in a.ids.split(",")] if a.ids else sorted(prompts))
    arms = a.arms.split(",")
    assert set(arms) <= set(ARMS), arms
    os.makedirs(acore.RUNS, exist_ok=True)
    mem = os.path.expanduser("~/.claude/projects/" + os.path.realpath(acore.TREE).replace("/", "-").replace("_", "-") + "/memory")
    if os.path.isdir(mem) and os.listdir(mem):
        raise SystemExit(f"auto-memory for the tree is not empty ({mem}); both arms would read it")
    coll = acore.collection()
    if not llcore.indexer_digests():
        raise SystemExit("no CE build digest readable: set LIVELOC_KUBE_CONTEXT to the CE cluster's kubectl context")
    if set(arms) & set(CE_ARMS):
        if not (llcore.load_json(llcore.COLLECTIONS)[acore.TREE_NAME].get("fresh_split") or {}).get("dev"):
            raise SystemExit(f"{acore.TREE_NAME} has not passed freshness.py --split dev; ce arm not started")
        if len(llcore.indexer_digests()) != 1:
            raise SystemExit("mcp-indexer-http must run exactly one digest (rollout in progress?)")
        llcore.map_workspace(acore.TREE, coll)
        info = acore.ce_server_info()  # what the CE server adds to the ce arm's context besides tool definitions
        if not info.get("serverInfo"):
            raise SystemExit(f"could not capture the CE server's initialize result: {str(info)[:200]}")
        llcore.save_json(os.path.join(acore.RUNS, "_ce_server.json"), {"captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                                                     "serverInfo": info.get("serverInfo"),
                                                                     "instructions": info.get("instructions")})
    llcore.write_mcp_configs()
    live = llcore.indexer_digests()[0] if set(arms) & set(CE_ARMS) else None
    manifest = acore.tree_manifest()
    bpath = os.path.join(acore.RUNS, "_batch.json")
    batch = llcore.load_json(bpath)
    if batch.get("manifest", manifest) != manifest:
        raise SystemExit(f"tree manifest {manifest} != batch {batch['manifest']}: the snapshot changed")
    # one CE build per arm: an arm that already has runs keeps the batch build it ran on; a new arm pins the live one
    builds = batch.get("builds") or {}
    for arm in (a for a in arms if a in CE_ARMS):
        if arm not in builds:
            ran = any(f.startswith(tuple(f"{i:02d}__{arm}__" for i in ids)) for f in os.listdir(acore.RUNS))
            builds[arm] = batch.get("build") if ran and batch.get("build") else live
        if builds[arm] != live:
            raise SystemExit(f"{arm} is pinned to CE build {builds[arm]}, live is {live}")
    llcore.save_json(bpath, {"manifest": manifest, "build": batch.get("build") or live, "builds": builds})
    jobs = [(prompts[i], arm, r) for r in range(1, a.repeats + 1) for i in ids for arm in arms]  # arms interleaved
    print(f"{len(jobs)} runs model={a.model} arms={arms} ids={ids} repeats={a.repeats} spent=${acore.spent():.2f} "
          f"cap=${a.cap} digests={llcore.indexer_digests()}", flush=True)
    lock, inflight = threading.Lock(), [0]

    def job(item, arm, rep):
        with lock:
            if acore.spent() + RESERVE[a.model] * (inflight[0] + 1) > a.cap:
                raise RuntimeError("cost cap reached")
            inflight[0] += 1
        try:
            return run_one(item, arm, a.model, rep, coll, builds.get(arm))
        finally:
            with lock:
                inflight[0] -= 1

    t0 = time.time()
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        futs = {ex.submit(job, *j): j for j in jobs}
        for f in cf.as_completed(futs):
            item, arm, rep = futs[f]
            try:
                r = f.result()
                print(f"#{item['id']:02d} {arm:6s} r{rep} ok={r['ok']} {r['subtype']} turns={r['num_turns']} "
                      f"${r['cost_bedrock']:.3f} wall={r['wall_s']}s tools={r['tool_calls']} ce={r['ce_calls']} "
                      f"cites={r['cites']['n']} halluc={r['cites']['hallucinated']}", flush=True)
            except Exception as e:  # noqa: BLE001 - one run failing must not stop the batch
                print(f"EXC #{item['id']:02d} {arm} r{rep}: {e!r}", flush=True)
    same = acore.tree_manifest() == manifest
    print(f"done in {time.time() - t0:.0f}s; spent=${acore.spent():.2f}; digests at end {llcore.indexer_digests()}; "
          f"tree manifest {manifest} {'unchanged' if same else 'CHANGED during the batch'}", flush=True)


if __name__ == "__main__":
    main()
