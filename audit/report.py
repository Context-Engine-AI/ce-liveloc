#!/usr/bin/env python3
"""Paired CE vs no-CE statistics for the GrapeRoot-Gitea audit A/B, one table set per model.

Usage: report.py --out results/audit-graperoot-gitea-smoke.md [--title smoke] [--models sonnet55,haiku55] [--smoke | --ids ...]

Unit = prompt. Runs pair by (prompt, repeat); per prompt the paired deltas (ce - normal) are averaged over repeats,
then: mean delta, prompt-clustered bootstrap 95% CI (10,000 draws, harness/stats.py), exact two-sided sign test over
prompts (zero deltas dropped), and Holm-adjusted p across every metric of the model's table. Descriptives use paired
runs only. Judge preference is +1 ce win / 0 tie / -1 normal win per pair.
"""
import argparse
import json
import math
import os
import statistics as st

import acore
import judge
import run_arms
from stats import boot_ci, holm  # harness/stats.py


def sign_p(d):
    pos, neg = sum(x > 0 for x in d), sum(x < 0 for x in d)
    n = pos + neg
    if not n:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(min(pos, neg) + 1)) / 2 ** n)


def load_judgments(model):
    out = {}
    for f in os.listdir(acore.JUDGE) if os.path.isdir(acore.JUDGE) else []:
        if f.endswith(".json") and f"__{model}__" in f:
            j = json.load(open(os.path.join(acore.JUDGE, f)))
            if j.get("verdict"):
                out[(j["id"], j["rep"], j["judge"], j.get("treatment", "ce"))] = j
    return out


RUN_METRICS = [  # (label, getter); None = undefined for that run
    ("turns", lambda r: r["num_turns"]),
    ("total tokens", lambda r: r["total_tokens"]),
    ("output tokens", lambda r: r["usage"]["output_tokens"]),
    ("$ / run (Bedrock list)", lambda r: r["cost_bedrock"]),
    ("wall s", lambda r: r["wall_s"]),
    ("tool calls", lambda r: r["tool_calls"]),
    ("hallucinated-citation rate", lambda r: r["cites"]["halluc_rate"]),
    ("citations (descriptive)", lambda r: r["cites"]["n"]),
]
JUDGE_METRICS = [  # (label, getter(j, treatment)); headline first, counts are descriptive
    ("preference (+1 treatment win, −1 normal win)", lambda j, t: {t: 1, "tie": 0, "normal": -1}[j["winner_arm"]]),
    ("overall score (1–10)", lambda j, t: j["by_arm"][t]["overall"] - j["by_arm"]["normal"]["overall"]),
    ("correctness score (1–10)", lambda j, t: j["by_arm"][t]["correctness"] - j["by_arm"]["normal"]["correctness"]),
    ("specificity score (1–10)", lambda j, t: j["by_arm"][t]["specificity"] - j["by_arm"]["normal"]["specificity"]),
    ("false claims per answer", lambda j, t: j["by_arm"][t]["false"] - j["by_arm"]["normal"]["false"]),
    ("verified findings per answer (descriptive)", lambda j, t: j["by_arm"][t]["verified"] - j["by_arm"]["normal"]["verified"]),
]
ARMS = run_arms.ARMS


def paired(by_prompt):
    """{prompt: [delta per repeat]} -> (n prompts, mean, lo, hi, sign p) over per-prompt mean deltas."""
    d = [st.mean(v) for v in by_prompt.values() if v]
    if not d:
        return None
    lo, hi = boot_ci(d) if len(d) > 1 else (d[0], d[0])
    return len(d), st.mean(d), lo, hi, sign_p(d)


def g(v):
    return f"{v:+,.0f}" if abs(v) >= 1000 else f"{v:+.3g}"


def model_section(model, ids, judges):
    runs = {k: r for k, r in judge.load_runs(model).items() if k[0] in ids}
    js = load_judgments(model)
    L = [f"## {model} (`{acore.MODELS[model]}`)\n"]
    if not runs:
        return L + ["No valid runs yet.\n"]
    arms = [a for a in ARMS if any(k[1] == a for k in runs)]
    treats = [a for a in arms if a != "normal"]
    keys = sorted({(i, rep) for i, _a, rep in runs})
    full = [k for k in keys if all((k[0], a, k[1]) in runs for a in arms)]
    partial = sorted(f"#{i} r{rep} {a}" for i, a, rep in runs if (i, rep) not in full)
    mism = [f"#{i} r{rep}" for i, rep in full if len({(runs[(i, a, rep)]["init"].get("claude_code_version"),
                                                         runs[(i, a, rep)]["init"].get("model")) for a in arms}) > 1]
    L.append(f"Complete sets (every arm present): {len(full)} over {len({i for i, _ in full})} prompts. Valid runs outside a "
             f"complete set (excluded from descriptives, still used in any pair they complete): {', '.join(partial) or 'none'}. "
             f"Sets whose runs differ in Claude Code version or model: {', '.join(mism) or 'none'}.\n")
    L.append("| arm | runs | turns mean (median) | total tokens mean | input / cache-write / cache-read / output (mean) | "
             "$/run | $ total | wall s mean | tool calls/run | Read calls/run | CE calls/run | runs calling CE | "
             "CE calls with include_snippet=false | CE call s median / p95 | CE calls failed | Skill calls/run | "
             "runs at a ceiling | extra attempts | citations/run | cited regions found | hallucinated citations (pooled) |")
    L.append("|---|" + "---|" * 20)
    for arm in arms:
        rs = [runs[(i, arm, rep)] for i, rep in full]
        if not rs:
            continue
        u = {k: st.mean(r["usage"][k] for r in rs) for k in rs[0]["usage"]}
        c = {k: sum(r["cites"][k] for r in rs) for k in ("n", "ok", "hallucinated", "ambiguous")}
        cc = [x for r in rs if r["ce_calls"] for x in acore.ce_calls(os.path.join(
            acore.RUNS, f"{r['id']:02d}__{r['arm']}__{r['model']}__r{r['rep']}.jsonl"))]
        lat = sorted(x[1] for x in cc)
        cec = (f"{sum(x[2] is False for x in cc)}/{len(cc)} | {lat[len(lat) // 2]:.1f} / {lat[int(len(lat) * .95)]:.1f} | "
               f"{sum(x[3] for x in cc)}") if cc else "— | — | —"
        L.append(f"| {arm} | {len(rs)} | {st.mean(r['num_turns'] for r in rs):.1f} ({st.median(r['num_turns'] for r in rs):.0f}) | "
                 f"{st.mean(r['total_tokens'] for r in rs):,.0f} | {u['input_tokens']:,.0f} / {u['cache_creation_input_tokens']:,.0f} / "
                 f"{u['cache_read_input_tokens']:,.0f} / {u['output_tokens']:,.0f} | {st.mean(r['cost_bedrock'] for r in rs):.4f} | "
                 f"{sum(r['cost_bedrock'] for r in rs):.2f} | {st.mean(r['wall_s'] for r in rs):.0f} | "
                 f"{st.mean(r['tool_calls'] for r in rs):.1f} | {st.mean(r['tool_calls_by_name'].get('Read', 0) for r in rs):.2f} | "
                 f"{st.mean(r['ce_calls'] for r in rs):.2f} | "
                 f"{sum(1 for r in rs if r['ce_calls'])}/{len(rs)} | {cec} | {st.mean(r.get('skill_calls') or 0 for r in rs):.2f} | "
                 f"{sum(1 for r in rs if r['hit_ceiling'])} | {sum((r.get('attempt') or 1) - 1 for r in rs)} | "
                 f"{c['n'] / len(rs):.1f} | {c['ok'] / c['n'] if c['n'] else 0:.3f} | "
                 f"{c['hallucinated']}/{c['n'] - c['ambiguous']} = "
                 f"{c['hallucinated'] / (c['n'] - c['ambiguous']) if c['n'] - c['ambiguous'] else 0:.3f} |")
    tools = sorted({t for r in runs.values() for t in r["tool_calls_by_name"]})
    L.append("\nTool calls per run by tool (mean over complete sets):\n")
    L.append("| arm | " + " | ".join(t.replace("mcp__context-engine__", "ce:") for t in tools) + " |")
    L.append("|---|" + "---|" * len(tools))
    for arm in arms:
        rs = [runs[(i, arm, rep)] for i, rep in full]
        if rs:
            L.append(f"| {arm} | " + " | ".join(f"{st.mean(r['tool_calls_by_name'].get(t, 0) for r in rs):.2f}" for t in tools) + " |")
    pairs = {t: sorted({(i, rep) for i, a, rep in runs if a == t and (i, "normal", rep) in runs}) for t in treats}
    if js:
        L.append("\nBlind pairwise judges, each treatment arm against normal on its own pairs (order balanced: Opus sees the "
                 "treatment as report X in half the pairs per repeat, GPT sees every pair in the opposite order):\n")
        L.append("| judge | treatment | pairs | treatment wins | ties | normal wins | report-X wins (position check) | "
                 "overall t / n | correctness t / n | false claims/answer t / n | verified/answer t / n |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for t in treats:
            for jn in judges:
                jj = [js[(i, rep, jn, t)] for i, rep in pairs[t] if (i, rep, jn, t) in js]
                if not jj:
                    continue
                w = [j["winner_arm"] for j in jj]
                m = lambda arm, k: st.mean(j["by_arm"][arm][k] for j in jj)  # noqa: E731
                L.append(f"| {jn} ({jj[0]['judge_model']}) | {t} | {len(jj)} | {w.count(t)} | {w.count('tie')} | {w.count('normal')} | "
                         f"{sum(1 for j in jj if j['verdict']['winner'] == 'X')} | {m(t, 'overall'):.2f} / {m('normal', 'overall'):.2f} | "
                         f"{m(t, 'correctness'):.2f} / {m('normal', 'correctness'):.2f} | {m(t, 'false'):.2f} / {m('normal', 'false'):.2f} | "
                         f"{m(t, 'verified'):.2f} / {m('normal', 'verified'):.2f} |")
            if len(judges) > 1:
                both = [(js[(i, rep, judges[0], t)]["winner_arm"], js[(i, rep, judges[1], t)]["winner_arm"])
                        for i, rep in pairs[t] if (i, rep, judges[0], t) in js and (i, rep, judges[1], t) in js]
                if both:
                    L.append(f"\n{t}: the two judges pick the same winner in {sum(a == b for a, b in both)}/{len(both)} pairs.")
    rows = []
    for t in treats:
        for label, get in RUN_METRICS:
            d = {}
            for i, rep in pairs[t]:
                a, b = get(runs[(i, t, rep)]), get(runs[(i, "normal", rep)])
                if a is not None and b is not None:
                    d.setdefault(i, []).append(a - b)
            rows.append((f"{t} − normal: {label}", paired(d)))
        for jn in judges:
            for label, get in JUDGE_METRICS:
                d = {}
                for i, rep in pairs[t]:
                    if (i, rep, jn, t) in js:
                        d.setdefault(i, []).append(get(js[(i, rep, jn, t)], t))
                if d:
                    rows.append((f"{t} − normal: {jn}: {label}", paired(d)))
    adj = holm([r[1][4] if r[1] else 1.0 for r in rows])
    L.append("\nPaired deltas, treatment − normal (n = prompts; repeats averaged within a prompt; Holm across every row of "
             "this table):\n")
    L.append("| metric | n | mean Δ | 95% CI | sign-test p | Holm p |")
    L.append("|---|---|---|---|---|---|")
    for (label, p), ph in zip(rows, adj):
        L.append(f"| {label} | — | — | — | — | — |" if p is None else
                 f"| {label} | {p[0]} | {g(p[1])} | [{g(p[2])}, {g(p[3])}] | {p[4]:.3f} | {ph:.3f} |")
    return L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="full")
    ap.add_argument("--models", default="sonnet55,haiku55")
    ap.add_argument("--ids")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--judges", default="opus,gpt")
    ap.add_argument("--notes", help="markdown file appended verbatim (caveats written after reading the results)")
    a = ap.parse_args()
    prompts = acore.load_prompts()
    ids = set(acore.SMOKE_IDS if a.smoke else ([int(x) for x in a.ids.split(",")] if a.ids else prompts))
    judges, models = a.judges.split(","), a.models.split(",")
    allruns = [r for m in models for k, r in judge.load_runs(m).items() if k[0] in ids]
    arm_digests = {}
    for r in allruns:
        if r["arm"] in run_arms.CE_ARMS:
            arm_digests.setdefault(r["arm"], set()).update((r["digest_start"] or []) + (r["digest_end"] or []))
    arm_digests = {k: sorted(v) for k, v in arm_digests.items()}
    cc = sorted({(r.get("init") or {}).get("claude_code_version") for r in allruns} - {None})
    jfiles = [os.path.join(acore.JUDGE, f) for f in sorted(os.listdir(acore.JUDGE))] if os.path.isdir(acore.JUDGE) else []
    jrecs = [j for j in (json.load(open(f)) for f in jfiles if f.endswith(".json")) if j["id"] in ids and j["model"] in models]
    server = acore.llcore.load_json(os.path.join(acore.RUNS, "_ce_server.json"))
    batch = acore.llcore.load_json(os.path.join(acore.RUNS, "_batch.json"))
    tree_ok = not batch.get("manifest") or acore.tree_manifest() == batch["manifest"]
    L = [f"# CE vs no-CE on GrapeRoot's Gitea audit prompts ({a.title})\n",
         f"- Prompts: {acore.GRAPEROOT_URL} @ `{acore.GRAPEROOT_SHA}`, `{acore.GRAPEROOT_PROMPTS}` (read at run time, not "
         f"copied into this repo); prompt ids {sorted(ids)}.",
         f"- Code: go-gitea/gitea snapshot `{acore.SNAPSHOT}` (tree `{acore.TREE_NAME}`, manifest "
         f"`{batch.get('manifest', '?')}`{'' if tree_ok else ' — CHANGED since the runs'}); CE collection `{acore.collection()}`.",
         "- CE build (mcp-indexer-http image digest, pinned per arm): " + ("; ".join(
             f"{arm}: " + ", ".join(f"`{d}`" for d in ds) for arm, ds in sorted(arm_digests.items())) or "—") + ".",
         f"- Agent: Claude Code {', '.join(cc)} headless. Judges: Claude Opus 5.5 (`{acore.JUDGE_OPUS}`, Bedrock, `claude -p`, "
         "no tools) and GPT-6.1 (`gpt-6.1-sol`, `codex exec`, shell and web off).",
         f"- Dollars: {acore.RATES_NOTE} This report: agent runs ${sum(r['cost_bedrock'] for r in allruns):.2f}, "
         f"Opus judgments ${sum(j.get('cost_bedrock') or 0 for j in jrecs):.2f}; all audit spend so far incl. set-aside runs "
         f"${acore.spent():.2f}. GPT-6.1 judge tokens are billed to a separate Azure deployment and not in these dollars "
         f"({sum((j.get('usage') or {}).get('input_tokens', 0) for j in jrecs if j['judge'] == 'gpt'):,} input / "
         f"{sum((j.get('usage') or {}).get('output_tokens', 0) for j in jrecs if j['judge'] == 'gpt'):,} output tokens).",
         "- Arms: same prompt (GrapeRoot's text + one line asking for `path:line` citations); `--setting-sources \"\"` so no "
         "CLAUDE.md/AGENTS.md (the Gitea tree has both), user hooks or plugins; no stop, turn-budget or tool-use instructions; "
         "same built-in tools and deny list; identical silent ceilings (150 turns, $8, 45 min). `ce` adds only the CE MCP "
         "server, i.e. its tool definitions and the server's own initialize instructions "
         + (f"({len(server.get('instructions') or '')} chars, see `-ce-server.json`)." if server.get("serverInfo")
            else "(not captured).") + "",
         "- Skill arms (added after the smoke run): `ce` + what the VS Code extension installs with `mcpClaudeSkillEnabled` "
         "(installSkillClaude): the skill folder copied verbatim plus the line \"# Important, always use skill "
         "`context-engine`\" prepended to CLAUDE.md, both in a fresh per-run `CLAUDE_CONFIG_DIR` loaded with "
         "`--setting-sources user`, plus the Skill tool; a run where Claude Code did not list the skill is invalid. "
         + " ".join(f"`{arm}`: SKILL.md sha256 `{sha[:16]}…` from `{os.path.basename(repo)}@{commit or 'working copy, ' + _f}`."
                    for arm, (repo, commit, _f, sha) in run_arms.SKILLS.items())
         + " **`ce-skill-shipped` is what the extension ships (Context-Engine-Extension 187f674, release 2.3.27). "
         "`ce-skill-stale` is an older copy from the retired monorepo, run by mistake first; it is reported as a finding "
         "about that file, not as the shipped product.** It has no opening frontmatter `---` and says \"NEVER use Read "
         "File or grep for exploration\". `ce-skill-shipped` ran on Sonnet only, after the other arms, so its wall time is "
         "not comparable; its tokens, cost and quality are paired with the same normal runs. `ce-skill-slim` (27 lines, written "
         "after a diagnosis of the shipped skill's losses) ran the same way, Sonnet only, later still, on its own frozen CE "
         "build; its wall time is not comparable either.",
         "- Scoring: a hallucinated citation is a cited file missing from the pinned tree or a line past its end (`audit/cites.py`); "
         "the judges see both answers (tool mentions scrubbed) plus the pinned code at every cited location, pooled, and grade "
         "verified correctness, specificity and false claims with a rubric that gives volume no credit (`audit/judge.py`).\n"]
    for m in models:
        L += model_section(m, ids, judges) + [""]
    if a.notes:
        L.append(open(a.notes).read())
    out = "\n".join(L) + "\n"
    print(out)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    open(a.out, "w").write(out)
    stem = a.out[:-3]
    with open(stem + "-runs.jsonl", "w") as fh:  # per-run records incl. answers; no prompt text, no raw traces
        for r in sorted(allruns, key=lambda r: (r["model"], r["id"], r["rep"], r["arm"])):
            fh.write(json.dumps({k: v for k, v in r.items() if k not in ("stderr_tail", "model_usage")}, sort_keys=True) + "\n")
    with open(stem + "-judgments.jsonl", "w") as fh:
        for j in jrecs:
            fh.write(json.dumps({k: v for k, v in j.items() if k != "raw"}, sort_keys=True) + "\n")
    if server:
        acore.llcore.save_json(stem + "-ce-server.json", server)


if __name__ == "__main__":
    main()
