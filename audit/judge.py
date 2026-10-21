#!/usr/bin/env python3
"""Blind pairwise judging of normal vs ce answers to the same prompt, by two judges from different model families.

Usage: judge.py --model sonnet55 [--judges opus,gpt] [--smoke | --ids 1,4] [--repeats 3] [--jobs 4] [--cap 450]

Per (prompt, repeat) with a valid run in both arms: the two answers go in as report X / Y with tool and CE mentions
scrubbed (path and citation tokens are protected first). Order is balanced: per (model, repeat) a seeded half of
the prompts show ce as X to Opus, and GPT always sees the opposite order, so each pair is judged once in each order; plus the pinned code at every location either answer cites (pooled, so the
evidence does not reveal which answer cited what) and the list of cited locations that do not exist.
Judges: opus = Claude Opus 5.5 on Bedrock via `claude -p` (no tools, no settings, our system prompt, JSON schema);
        gpt  = GPT-6.1 via `codex exec` (shell tool and web search off, read-only sandbox, empty cwd, clean CODEX_HOME:
               no MCP, AGENTS.md or hooks); a verdict that still ran a command is discarded and re-asked.
The rubric (RUBRIC) is ours: verified correctness, specificity, false claims; volume earns nothing by itself.
"""
import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import random
import re
import subprocess
import threading
import time
import tomllib

import acore
import cites
from acore import llcore

RUBRIC = """You are a senior Go engineer grading two code-audit reports about the same repository (Gitea) in a blind evaluation. Both reports answer the same task. You also get the repository's code at every location either report cites, taken from the exact snapshot the reports were written against, and a list of cited locations that do not exist.

Judge only from the task, the two reports and the code evidence. Do not run tools or commands.

For each report, go through its distinct findings. A finding is one specific claimed problem at a specific place; several lines making one claim count once. Put each finding in exactly one bucket:
- verified: the evidence shows the cited code and it does have the problem the report describes (for code-quality tasks: it does fit the description).
- plausible: concrete and consistent with the evidence, but the evidence shown is not enough to confirm it (for example the check might happen in a caller that is not shown).
- unsupported: no concrete location, or too vague to check.
- false: the evidence contradicts it, the cited location does not exist or does not contain what is described, or the reasoning about the code is wrong.
Items a report explicitly sets aside as reviewed and fine are not findings, but a dismissal the evidence shows to be wrong counts as false.

Then score each report from 1 to 10:
- correctness: how much of what it claims is true. Mostly verified with nothing false scores high; every false claim costs a lot; plausible findings are neutral (they neither add nor subtract).
- specificity: whether each finding pins down the file, the line and the mechanism that makes it a problem.
- overall: which report a maintainer would rather act on: real problems, located precisely, no wild goose chases.

Rules: volume earns nothing by itself. More findings help only when they are verified; unsupported and false findings count against a report. Do not reward length, the number of files listed, hedging, boilerplate or generic advice. Ignore formatting and writing style. Tool names are redacted as [tool]; ignore how a report says it searched.
winner: "X" or "Y" if one report is better overall; "tie" only if they are genuinely equivalent. reason: at most 40 words.
Reply with the JSON object only."""

_SCORE = {"type": "object", "additionalProperties": False,
          "required": ["verified", "plausible", "unsupported", "false", "correctness", "specificity", "overall"],
          "properties": {**{k: {"type": "integer", "description": "count"} for k in ("verified", "plausible", "unsupported", "false")},
                         **{k: {"type": "integer", "description": "1-10"} for k in ("correctness", "specificity", "overall")}}}
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["x", "y", "winner", "reason"],
          "properties": {"x": _SCORE, "y": _SCORE, "winner": {"type": "string", "enum": ["X", "Y", "tie"]},
                         "reason": {"type": "string"}}}

SCRUB = [(re.compile(p, f), "[tool]") for p, f in (  # tool and search-method words only, not Gitea vocabulary
    (r"\b(?:the )?(?:read|bash|glob|grep) tool\b", re.I), (r"mcp__[\w-]+", 0), (r"\bcontext[\s_-]?engine\b", re.I), (r"\bCE\b", 0), (r"\bMCP\b", re.I),
    (r"\b(?:repo_search|symbol_graph|context_answer|graph_query|cross_repo_search|search_tests_for|search_config_for)\b", 0),
    (r"\bsymbol[\s_-]graph\b|\bgraph[\s_-]quer(?:y|ies)\b", re.I), (r"\b(?:qdrant\w*|helion|helix\w*)\b", re.I),
    (r"\b(?:semantic(?:ally)?|vector|hybrid|lexical|keyword|literal|regex|pattern|full[\s-]text|indexed)"
     r"[\s-]+search(?:es|ed|ing)?\b", re.I),
    (r"(?<!git )\b(?:ripgrep|rg|e?grep)\b", re.I), (r"\b(?:Glob|Bash|Grep)\b", 0), (r"\bzsh\b", re.I),
    (r"`find [^`]*`", 0))]
# protected from scrubbing: file paths and names with a tree extension (modules/git/grep.go stays intact)
_KEEP = re.compile(cites._PATH.replace("(?P<path>", "(?:").replace("(?P<ext>", "(?:") + r"(?::L?\d+)?")
MAX_ANSWER = 40_000
JUDGE_CWD = "/tmp/ce-audit/judge-cwd"
CODEX_HOME = "/tmp/ce-audit/codex-home"


def scrub(text, tree=acore.TREE):
    for root in {os.path.realpath(tree), tree}:
        text = text.replace(root + "/", "")
    text = text.replace(acore.collection(), "[tool]").replace("/work/", "")
    kept = []  # paths and file names (e.g. modules/git/grep.go) are code, not method text: protect them

    def keep(m):
        kept.append(m.group(0))
        return f"\x00{len(kept) - 1}\x00"
    exts = cites.tree_index(tree)[0]
    text = _KEEP.sub(lambda m: keep(m) if os.path.splitext(m.group(0).split(":")[0])[1][1:].lower() in exts else m.group(0), text)
    for rx, sub in SCRUB:
        text = rx.sub(sub, text)
    text = re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], text)
    if len(text) > MAX_ANSWER:
        text = text[:MAX_ANSWER] + "\n[report truncated for length]"
    return text


def packet(item, rx, ry):
    ev, dropped = cites.evidence(acore.TREE, [tuple(r) for r in rx["cite_rows"]], [tuple(r) for r in ry["cite_rows"]])
    body = (f"TASK (given verbatim to both):\n<task>\n{acore.prompt_text(item)}\n</task>\n\n"
            f"REPORT X:\n<report_x>\n{scrub(rx['answer'])}\n</report_x>\n\n"
            f"REPORT Y:\n<report_y>\n{scrub(ry['answer'])}\n</report_y>\n\n"
            f"CODE EVIDENCE (pinned snapshot; every region either report cites, pooled; line-numbered):\n<evidence>\n{ev}\n</evidence>")
    return body, dropped


def parse_json(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    return json.loads(m.group(0)) if m else None


def judge_opus(body):
    cmd = ["claude", "-p", "--system-prompt", RUBRIC, "--json-schema", json.dumps(SCHEMA), "--tools", "",
           "--setting-sources", "", "--strict-mcp-config", "--mcp-config", llcore.NO_MCP_CFG, "--model", acore.JUDGE_OPUS,
           "--effort", "high", "--output-format", "json", "--no-session-persistence"]
    env = {**os.environ, **llcore.bedrock_env(), "DISABLE_AUTOUPDATER": "1"}
    p = subprocess.run(cmd, input=body, capture_output=True, text=True, env=env, cwd=JUDGE_CWD, timeout=1200)
    res = json.loads(p.stdout)
    verdict = res.get("structured_output") or parse_json(res.get("result"))
    ev = [{"type": "assistant", "message": {"id": "j", "usage": res.get("usage") or {}}}, res]
    return verdict, {"usage": res.get("usage"), "cost_cc": res.get("total_cost_usd"),
                     "cost_bedrock": acore.run_cost(ev, acore.JUDGE_OPUS)[0], "raw": (res.get("result") or "")[-2000:]}


def codex_home():
    """Minimal CODEX_HOME with only the model provider from ~/.codex/config.toml (no MCP servers, AGENTS.md, hooks)."""
    os.makedirs(CODEX_HOME, mode=0o700, exist_ok=True)
    src = tomllib.load(open(os.path.expanduser("~/.codex/config.toml"), "rb"))
    prov = src["model_providers"][src["model_provider"]]

    def tv(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, dict):
            return "{ " + ", ".join(f"{json.dumps(k)} = {tv(x)}" for k, x in v.items()) + " }"
        return json.dumps(v)
    lines = [f"model = {json.dumps(src['model'])}", f"model_provider = {json.dumps(src['model_provider'])}",
             f"model_catalog_json = {json.dumps(src['model_catalog_json'])}", 'model_reasoning_effort = "high"',
             'approval_policy = "never"', 'sandbox_mode = "read-only"', 'web_search = "disabled"',
             "[features]", "shell_tool = false", "unified_exec = false", f"[model_providers.{src['model_provider']}]"]
    lines += [f"{k} = {tv(v)}" for k, v in prov.items()]
    path = os.path.join(CODEX_HOME, "config.toml")
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    with open(os.path.join(CODEX_HOME, "schema.json"), "w") as fh:
        json.dump(SCHEMA, fh)
    return src["model"]


def judge_gpt(body, tag):
    out = os.path.join(CODEX_HOME, f"out-{tag}.txt")
    cmd = ["codex", "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only", "-C", JUDGE_CWD,
           "--output-schema", os.path.join(CODEX_HOME, "schema.json"), "-o", out, "--json", "-"]
    p = subprocess.run(cmd, input=RUBRIC + "\n\n" + body, capture_output=True, text=True, timeout=1800,
                       env={**os.environ, "CODEX_HOME": CODEX_HOME})
    usage, commands = {}, 0
    for line in p.stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "turn.completed":
            usage = ev.get("usage") or usage
        if (ev.get("item") or {}).get("type") == "command_execution" and ev.get("type") == "item.started":
            commands += 1
    text = open(out).read() if os.path.exists(out) else ""
    if os.path.exists(out):
        os.remove(out)
    return parse_json(text), {"usage": usage, "commands_run": commands, "returncode": p.returncode,
                              "raw": text[-2000:], "stderr_tail": p.stderr[-500:] if not text else ""}


def load_runs(model):
    runs = {}
    for f in os.listdir(acore.RUNS) if os.path.isdir(acore.RUNS) else []:
        if f.endswith(".json") and ".invalid-" not in f and not f.startswith("_"):
            r = json.load(open(os.path.join(acore.RUNS, f)))
            if r["model"] == model and r.get("ok") and r.get("digest_valid") is not False and r.get("ce_connected") is not False:
                c = cites.check(r["answer"])  # always re-scored with the current parser (deterministic)
                r["cites"], r["cite_rows"] = {k: v for k, v in c.items() if k != "rows"}, c["rows"]
                runs[(r["id"], r["arm"], r["rep"])] = r
    return runs


def ce_is_x(ids, pid, model, rep, judge):
    """Balanced order: a seeded half of the prompts per (model, repeat) show ce as X to Opus; GPT gets the opposite."""
    ranked = sorted(ids, key=lambda i: hashlib.sha256(f"{i}:{model}:{rep}".encode()).hexdigest())
    first = pid in ranked[:len(ranked) // 2] or bool(len(ranked) % 2 and pid == ranked[len(ranked) // 2]
                                                      and random.Random(f"{model}:{rep}").random() < 0.5)
    return first if judge == "opus" else not first


def judge_pair(item, model, rep, rn, rc, judge, gpt_model, ids):
    base = os.path.join(acore.JUDGE, f"{item['id']:02d}__{model}__r{rep}__{judge}")
    cex = ce_is_x(ids, item["id"], model, rep, judge)
    rx, ry = (rc, rn) if cex else (rn, rc)
    body, dropped = packet(item, rx, ry)
    sha = hashlib.sha256(body.encode()).hexdigest()[:16]
    prev = json.load(open(base + ".json")) if os.path.exists(base + ".json") else {}
    if prev.get("verdict") and prev.get("packet_sha256") == sha:
        return prev
    ce_is_x_ = cex
    t0 = time.time()
    verdict, meta = judge_opus(body) if judge == "opus" else judge_gpt(body, f"{item['id']}-{model}-{rep}")
    if judge == "gpt" and meta.get("commands_run"):  # must judge from the packet alone
        verdict = None
    side = {"x": "ce" if ce_is_x_ else "normal", "y": "normal" if ce_is_x_ else "ce"}
    rec = {"id": item["id"], "model": model, "rep": rep, "judge": judge,
           "judge_model": acore.JUDGE_OPUS if judge == "opus" else gpt_model, "ce_is_x": ce_is_x_,
           "packet_sha256": sha, "packet_chars": len(body), "attempts": prev.get("attempts", 0) + 1,
           "evidence_dropped": dropped, "wall_s": round(time.time() - t0, 1), "verdict": verdict,
           "runs": {"normal": f"{rn['id']:02d}__normal__{model}__r{rep}", "ce": f"{rc['id']:02d}__ce__{model}__r{rep}"},
           # every attempt was paid for, including a judgment replaced after a packet change
           "cost_bedrock": (prev.get("cost_bedrock") or 0.0) + meta.pop("cost_bedrock", 0.0), **meta}
    if verdict:
        rec["by_arm"] = {side["x"]: verdict["x"], side["y"]: verdict["y"]}
        rec["winner_arm"] = "tie" if verdict["winner"] == "tie" else side[verdict["winner"].lower()]
    llcore.save_json(base + ".json", rec)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, choices=sorted(acore.MODELS))
    ap.add_argument("--judges", default="opus,gpt")
    ap.add_argument("--ids")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--cap", type=float, default=450.0)
    a = ap.parse_args()
    prompts = acore.load_prompts()
    ids = acore.SMOKE_IDS if a.smoke else ([int(x) for x in a.ids.split(",")] if a.ids else sorted(prompts))
    judges = a.judges.split(",")
    batch = llcore.load_json(os.path.join(acore.RUNS, "_batch.json"))
    if batch.get("manifest") and acore.tree_manifest() != batch["manifest"]:
        raise SystemExit("the tree changed since the runs: code evidence would not match what the agents saw")
    os.makedirs(acore.JUDGE, exist_ok=True)
    os.makedirs(JUDGE_CWD, exist_ok=True)
    gpt_model = codex_home() if "gpt" in judges else None
    runs = load_runs(a.model)
    pairs = [(prompts[i], r) for r in range(1, a.repeats + 1) for i in ids
             if (i, "normal", r) in runs and (i, "ce", r) in runs]
    print(f"{len(pairs)} pairs x {judges} model={a.model} spent=${acore.spent():.2f} cap=${a.cap}", flush=True)
    lock = threading.Lock()

    def job(item, rep, judge):
        if judge == "opus":
            with lock:
                if acore.spent() + 0.5 > a.cap:
                    raise RuntimeError("cost cap reached")
        return judge_pair(item, a.model, rep, runs[(item["id"], "normal", rep)], runs[(item["id"], "ce", rep)], judge,
                          gpt_model, ids)  # order ranked over the requested ids, so a late rerun moves no other pair

    with cf.ThreadPoolExecutor(a.jobs) as ex:
        futs = {ex.submit(job, item, rep, j): (item["id"], rep, j) for item, rep in pairs for j in judges}
        for f in cf.as_completed(futs):
            pid, rep, j = futs[f]
            try:
                r = f.result()
                print(f"#{pid:02d} r{rep} {j:4s} winner={r.get('winner_arm')} ce_is_x={r['ce_is_x']} "
                      f"chars={r['packet_chars']} ${r.get('cost_bedrock') or 0:.3f} {r['wall_s']}s "
                      f"cmds={r.get('commands_run', 0)}", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"EXC #{pid:02d} r{rep} {j}: {e!r}", flush=True)
    print(f"done; spent=${acore.spent():.2f}", flush=True)


if __name__ == "__main__":
    main()
