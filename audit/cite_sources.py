#!/usr/bin/env python3
"""Where each cited line number came from, read from the run's own trace.

Usage: cite_sources.py --model sonnet55 [--arms normal,ce,ce-skill-shipped,ce-skill-slim] [--out results/x.md]

Every citation that resolves in the tree (cites.py status ok) is put in the first class that holds its line, in order:
  numbered    shown with its line number: Read output, Grep content (-n), Bash `grep -n`/`rg -n` lines, `cat -n`/`nl`
  ce_number   a number CE stated for that file (a row's start_line / end_line / symbol_start_line, or `path:N` text)
  ce_snippet  inside a CE code snippet that matches the tree, at a number CE did not state: the agent counted
  unnumbered  printed without numbers (cat / sed / head / tail): the agent counted
  unseen      never shown to the agent at all
A range citation is classed by its first line.
"""
import argparse
import json
import os
import re
import shlex

import acore
import cites
import judge
import run_arms
import treesit
import score  # harness/score.py: read_view, grep_view, grep_lines, bash_view, ce_view, _ce_rows

CLASSES = ("numbered", "ce_number", "ce_snippet", "unnumbered", "unseen")
_NUM_KEYS = ("start_line", "end_line", "symbol_start_line", "startLine", "endLine", "line")


def _add(d, f, iv):
    for s, e in iv:
        d.setdefault(f, []).append((s, e))


def bash_numbered(tree, cmd, text):
    """Lines a Bash command printed with their numbers: multi-file `path:N:` grep lines, and `N:` / `N<tab>` lines
    when one grep -n / rg -n / cat -n / nl segment names exactly one file."""
    out = {f: list(v) for f, v in score.grep_lines(tree, text).items()}
    single, tab = [], False
    for seg in score.SEG_SPLIT.split(cmd or ""):
        first = seg.split("|")[0].strip()
        try:
            toks = shlex.split(first)
        except ValueError:
            toks = first.split()
        if not toks:
            continue
        numbered = (toks[0] in score.GREPS and (toks[0] == "rg" or any(re.fullmatch(r"-\w*n\w*", t) for t in toks))) \
            or toks[0] == "nl" or (toks[0] == "cat" and "-n" in toks)
        files = [f for f in (score._rel(tree, tree, t) for t in toks[1:]) if f]
        if numbered and len(files) == 1:
            single.append(files[0])
            tab = tab or toks[0] in ("nl", "cat")
    if len(set(single)) == 1:
        rx = re.compile(r"^\s*(\d+)\t" if tab else r"^(\d+)[:-]")
        for line in (text or "").split("\n"):
            m = rx.match(line)
            if m:
                out.setdefault(single[0], []).append((int(m.group(1)),) * 2)
    return out


def ce_numbers(tree, text):
    """{file: set of line numbers CE printed for it}."""
    try:
        obj = json.loads(text)
    except ValueError:
        try:
            obj = score.toon.loads(text)
        except (ValueError, IndexError, AttributeError):
            obj = None
    out = {}

    def walk(o):
        if isinstance(o, dict):
            p = next((o[k] for k in score._PATH_KEYS if isinstance(o.get(k), str)), None)
            f = treesit.resolve(tree, p) if p else ""
            if f:
                out.setdefault(f, set()).update(o[k] for k in _NUM_KEYS if type(o.get(k)) is int)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(obj)
    for m in cites.CITE.finditer(text or ""):  # refs printed as `path:N` text
        f = treesit.resolve(tree, m.group("path"))
        if f:
            out.setdefault(f, set()).add(int(m.group("a")))
    return out


def views(tree, trace_path, collection):
    """Per class, what the trace showed the agent: {class: {file: intervals or set}}."""
    v = {"numbered": {}, "ce_number": {}, "ce_snippet": {}, "unnumbered": {}}
    uses = {}
    for raw in open(trace_path):
        ev = json.loads(raw)["ev"]
        if ev.get("type") == "assistant":
            for b in ev.get("message", {}).get("content", []):
                if b.get("type") == "tool_use":
                    uses[b["id"]] = (b["name"], b.get("input") or {})
        elif ev.get("type") == "user" and isinstance(ev.get("message", {}).get("content"), list):
            results = [b for b in ev["message"]["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
            tur = ev.get("tool_use_result") if len(results) == 1 else None
            for b in results:
                if b.get("is_error"):
                    continue
                name, inp = uses.get(b.get("tool_use_id"), ("?", {}))
                text = score._text(b.get("content"))
                if name == "Read":
                    for f, iv in score.read_view(tree, inp, tur, text).items():
                        _add(v["numbered"], f, iv)
                elif name == "Grep":
                    for f, iv in score.grep_view(tree, inp, text).items():
                        _add(v["numbered"], f, iv)
                elif name == "Bash":
                    for f, iv in bash_numbered(tree, inp.get("command"), text).items():
                        _add(v["numbered"], f, iv)
                    for f, iv in score.bash_view(tree, inp, ".").items():  # "." holds no numbered lines: printers only
                        _add(v["unnumbered"], f, iv)
                elif name.startswith(score.CE_PREFIX):
                    for f, nums in ce_numbers(tree, text).items():
                        v["ce_number"].setdefault(f, set()).update(nums)
                    for f, iv in score.ce_view(tree, text, collection)[0].items():
                        _add(v["ce_snippet"], f, iv)
    return v


def classify(v, f, line):
    for c in ("numbered", "ce_number", "ce_snippet", "unnumbered"):
        got = v[c].get(f)
        if got and (line in got if isinstance(got, set) else any(s <= line <= e for s, e in got)):
            return c
    return "unseen"


def run_classes(rec, tree=acore.TREE):
    trace = os.path.join(acore.RUNS, f"{rec['id']:02d}__{rec['arm']}__{rec['model']}__r{rec['rep']}.jsonl")
    v = views(tree, trace, rec.get("collection"))
    counts = dict.fromkeys(CLASSES, 0)
    for _raw, a, _b, st, p in cites.check(rec["answer"], tree)["rows"]:
        if st == "ok":
            counts[classify(v, p, a)] += 1
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="sonnet55")
    ap.add_argument("--arms", default=",".join(a for a in run_arms.ARMS if a != "ce-skill-stale"))
    ap.add_argument("--out")
    a = ap.parse_args()
    runs = judge.load_runs(a.model)
    L = [f"### Where cited line numbers came from ({a.model}; pooled over valid citations, first matching source)\n",
         "| arm | runs | citations | " + " | ".join(CLASSES) + " |", "|---|---|---|" + "---|" * len(CLASSES)]
    for arm in a.arms.split(","):
        rs = [r for k, r in sorted(runs.items()) if k[1] == arm]
        if not rs:
            continue
        tot = dict.fromkeys(CLASSES, 0)
        for r in rs:
            for c, n in run_classes(r).items():
                tot[c] += n
        n = sum(tot.values())
        L.append(f"| {arm} | {len(rs)} | {n} | " + " | ".join(f"{tot[c] / n:.1%} ({tot[c]})" if n else "—" for c in CLASSES) + " |")
    out = "\n".join(L) + "\n"
    print(out)
    if a.out:
        open(a.out, "w").write(out)


if __name__ == "__main__":
    main()
