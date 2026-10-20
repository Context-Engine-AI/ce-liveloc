#!/usr/bin/env python3
"""Paired CE-LiveLoc statistics: every arm vs A, per task (the unit), one seed.

Usage: stats.py --split dev [--model haiku55] [--arms B,F,H] [--repos ...] [--out results/x.md]

Per metric and arm: mean of the per-task difference (arm - A), task-clustered bootstrap 95% CI (10,000 draws),
sign-flip permutation p (exact for n <= 16, else 20,000 Monte Carlo), Holm-adjusted across the arms.
TtG is compared on log2 (ratio = 2**mean); unfound runs are censored at CAP tokens, turns at MAX_TURNS + 1.
Only tasks with a valid run in every compared arm are paired.
"""
import argparse
import glob
import itertools
import json
import math
import os
import random
import statistics

import llcore

CAP = 200_000
TURN_CAP = 16
METRICS = [  # (name, getter, kind)
    ("TtG tokens (log2; ratio)", lambda r: math.log2(max(1, r["score"]["ttg"] if r["score"]["found"] else CAP)), "ratio"),
    ("turns-to-gold", lambda r: r["score"]["turns_to_gold"] if r["score"]["found"] else TURN_CAP, "diff"),
    ("found@8k", lambda r: float(r["score"]["found@8k"]), "diff"),
    ("found@32k", lambda r: float(r["score"]["found@32k"]), "diff"),
    ("found@100k", lambda r: float(r["score"]["found@100k"]), "diff"),
    ("answer file recall", lambda r: r["score"]["answer_recall"], "diff"),
    ("answer file precision", lambda r: r["score"]["answer_precision"], "diff"),
    ("answer fn/hunk hit", lambda r: float(r["score"]["fn_hit"]), "diff"),
    ("cost $/run", lambda r: r["cost_usd"] or 0.0, "diff"),
    ("total tokens/run", lambda r: r["total_tokens"], "diff"),
    ("wall s/run", lambda r: r["wall_s"], "diff"),
]


def load(split, model, repos):
    runs = {}
    for p in glob.glob(os.path.join(llcore.RUNS, f"*__{model}__s*.json")):
        if ".invalid-" in p or p.endswith(".hook.json"):
            continue
        r = json.load(open(p))
        if r.get("split") != split or (repos and r["repo"] not in repos) or not r.get("ok") or "score" not in r \
                or r.get("digest_valid") is False:
            continue
        runs[(r["id"], r["arm"])] = r
    return runs


def boot_ci(d, n=10_000, seed=7):
    rng = random.Random(seed)
    k = len(d)
    means = sorted(sum(d[rng.randrange(k)] for _ in range(k)) / k for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]


def perm_p(d, n=20_000, seed=11):
    obs = abs(sum(d))
    if len(d) <= 16:
        hits = sum(abs(sum(s * x for s, x in zip(signs, d))) >= obs - 1e-12
                   for signs in itertools.product((1, -1), repeat=len(d)))
        return hits / 2 ** len(d)
    rng = random.Random(seed)
    hits = sum(abs(sum(x if rng.random() < .5 else -x for x in d)) >= obs - 1e-12 for _ in range(n))
    return (hits + 1) / (n + 1)


def holm(ps):
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adj, run = [0.0] * len(ps), 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (len(ps) - rank) * ps[i]))
        adj[i] = run
    return adj


def fmt(v, kind):
    return f"x{2 ** v:.2f}" if kind == "ratio" else (f"{v:+.3f}" if abs(v) < 10 else f"{v:+.0f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    ap.add_argument("--model", default="haiku55")
    ap.add_argument("--arms", default="B,F,H,P")
    ap.add_argument("--repos")
    ap.add_argument("--out")
    a = ap.parse_args()
    runs = load(a.split, a.model, set(a.repos.split(",")) if a.repos else None)
    arms = [x for x in a.arms.split(",") if any(k[1] == x for k in runs)]
    ids = sorted({k[0] for k in runs})
    lines = [f"## CE-LiveLoc {a.split} — {a.model}\n"]
    # per-arm descriptives + adoption
    lines.append("| arm | runs | median TtG | found@32k | median turns-to-gold | answer recall | fn/hunk hit | $/run | "
                 "CE calls/run | runs using CE | hook hits/run | plugin denies/run |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for arm in ["A"] + arms:
        rs = [runs[(i, arm)] for i in ids if (i, arm) in runs]
        if not rs:
            continue
        ttg = [r["score"]["ttg"] if r["score"]["found"] else CAP for r in rs]
        ttt = [r["score"]["turns_to_gold"] if r["score"]["found"] else TURN_CAP for r in rs]
        use = sum(1 for r in rs if r["ce_calls"] or r["score"]["hook_hits"])
        lines.append(f"| {arm} | {len(rs)} | {statistics.median(ttg):,.0f} | {statistics.mean(r['score']['found@32k'] for r in rs):.2f} | "
                     f"{statistics.median(ttt):.0f} | {statistics.mean(r['score']['answer_recall'] for r in rs):.2f} | "
                     f"{statistics.mean(r['score']['fn_hit'] for r in rs):.2f} | {statistics.mean(r['cost_usd'] or 0 for r in rs):.4f} | "
                     f"{statistics.mean(r['ce_calls'] for r in rs):.2f} | {100 * use / len(rs):.0f}% | "
                     f"{statistics.mean(r['score']['hook_hits'] for r in rs):.2f} | "
                     f"{statistics.mean(r.get('denied') or 0 for r in rs):.2f} |")
    lines.append(f"\nTtG censored at {CAP:,} tokens and turns at {TURN_CAP} when gold was never seen.\n")
    lines.append("| metric | " + " | ".join(f"{x} − A (n, Δ [95% CI], p_holm)" for x in arms) + " |")
    lines.append("|---|" + "---|" * len(arms))
    for name, get, kind in METRICS:
        cells, ps, rows = [], [], []
        for arm in arms:
            pair = [i for i in ids if (i, "A") in runs and (i, arm) in runs]
            d = [get(runs[(i, arm)]) - get(runs[(i, "A")]) for i in pair]
            if not d:
                rows.append(None); ps.append(1.0); continue
            m = sum(d) / len(d)
            lo, hi = boot_ci(d)
            rows.append((len(d), m, lo, hi))
            ps.append(perm_p(d))
        adj = holm(ps)
        for row, p in zip(rows, adj):
            cells.append("—" if row is None else f"n={row[0]}, {fmt(row[1], kind)} [{fmt(row[2], kind)}, {fmt(row[3], kind)}], p={p:.3f}")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    out = "\n".join(lines) + "\n"
    print(out)
    if a.out:
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        open(a.out, "w").write(out)
        with open(a.out[:-3] + "-runs.jsonl", "w") as fh:  # compact per-run records (no raw traces)
            for k in sorted(runs):
                r = {x: v for x, v in runs[k].items() if x not in ("stderr_tail", "init")}
                r["claude_code_version"] = (runs[k].get("init") or {}).get("claude_code_version")
                fh.write(json.dumps(r, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
