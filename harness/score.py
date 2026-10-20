#!/usr/bin/env python3
"""Score CE-LiveLoc runs from their stream-json traces. Deterministic, no LLM judge.

Gold "seen" events (in trace order):
  Read of a gold file (any range) | Grep content-mode lines from a gold file | Bash output of grep -n lines from,
  or cat/sed/head/tail of, a gold file | CE tool result rows (or arm-H hook hits) whose returned code matches
  the snapshot file at the claimed lines. Path lists (Glob, Grep files mode) and location-only rows earn nothing.
Two levels: file (any gold-file code) and region (gold functions enclosing the changed lines, or changed lines +-3).

Token metrics (per API call, deduplicated by message id; prompt = input + cache write + cache read):
  ttg          exploration tokens to gold = prompt(call after the reveal) - prompt(first call)
               (context the agent accumulated, incl. the revealing result; fixed system/tool prefix excluded)
  ttg_billed   sum of prompt + output over calls up to the revealing one
  turns_to_gold  1-based index of the API call whose tool use revealed gold
Unfound runs get ttg = None (censored); stats cap them.

Usage: score.py RUN.json ...   re-scores runs in place
"""
import json
import os
import re
import shlex
import sys

import llcore
import toon
import treesit
from source_lines import verified_lines

CE_PREFIX = "mcp__context-engine__"
_CODE_KEYS = ("snippet", "code", "content", "text")
_PATH_KEYS = ("path", "file", "file_path", "rel_path")
BUDGETS = (8000, 32000, 100000)


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(x.get("text", "") for x in content if isinstance(x, dict))
    return json.dumps(content) if content is not None else ""


def _res(tree, p):
    return treesit.resolve(tree, p) if treesit.inside_tree(tree, p) else ""


def read_view(tree, inp, tur, text):
    fi = tur.get("file") if isinstance(tur, dict) and isinstance(tur.get("file"), dict) else {}
    f = _res(tree, fi.get("filePath") or (inp or {}).get("file_path"))
    if not f:
        return {}
    if fi.get("startLine") and fi.get("numLines"):
        s = int(fi["startLine"])
        return {f: [(s, s + int(fi["numLines"]) - 1)]}
    nums = [int(m.group(1)) for m in re.finditer(r"(?m)^\s*(\d+)[\t→]", text or "")]
    return {f: [(min(nums), max(nums))]} if nums else {}


def grep_lines(tree, text, single=""):
    """`path:N:` / `path-N-` lines (or `N:` for a single-file target) -> {file: intervals}."""
    out = {}
    for line in (text or "").split("\n"):
        if "[Omitted long" in line:
            continue
        if single:
            m = re.match(r"(\d+)[:-]", line)
            if m:
                out.setdefault(single, []).append((int(m.group(1)),) * 2)
            continue
        for m in re.finditer(r"([:-])(\d+)\1", line):
            f = _res(tree, line[:m.start()])
            if f:
                out.setdefault(f, []).append((int(m.group(2)),) * 2)
                break
    return {f: treesit.merge(v) for f, v in out.items()}


def grep_view(tree, inp, text):
    inp = inp or {}
    if inp.get("output_mode") != "content" or inp.get("-n") is False:
        return {}
    target = inp.get("path") or ""
    if target and not treesit.inside_tree(tree, target):
        return {}
    abs_t = target if os.path.isabs(target) else os.path.join(tree, target)
    return grep_lines(tree, text, treesit.resolve(tree, target) if target and os.path.isfile(abs_t) else "")


SEG_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\n)\s*")
PRINTERS = {"cat", "head", "tail", "sed", "nl", "less", "more", "awk", "bat"}
GREPS = {"grep", "egrep", "rg", "git-grep"}


def _rel(tree, cwd, p):
    p = (p or "").strip().strip("'\"")
    if not p or p.startswith("-"):
        return ""
    return _res(tree, p if os.path.isabs(p) else os.path.normpath(os.path.join(cwd, p)))


def _nlines(tree, f):
    offs = treesit._line_offsets(os.path.join(tree, f))[0]
    return len(offs) if offs else 1


def bash_view(tree, inp, text):
    """Code a Bash command printed: cat/sed/head/tail/nl of files (per `cd`), and grep -n style lines."""
    cmd = (inp or {}).get("command") or ""
    if not text.strip() or text.startswith("Permission to use Bash"):
        return {}
    cwd, cwds, view, single = tree, [tree], {}, []
    for seg in SEG_SPLIT.split(cmd):
        first = seg.split("|")[0].strip()
        try:
            toks = shlex.split(first)
        except ValueError:
            toks = first.split()
        if not toks:
            continue
        if toks[0] == "cd" and len(toks) > 1:
            cwd = os.path.normpath(os.path.join(cwd, toks[1].strip("'\"")))
            cwds.append(cwd)
            continue
        if toks[0] in GREPS:
            files = [f for f in (_rel(tree, cwd, t) for t in toks[1:]) if f]
            if len(files) == 1 and not any(t in ("-r", "-R", "-rn", "-Rn", "-nr", "-rni", "-H") for t in toks):
                single.append(files[0])
            continue
        if toks[0] in PRINTERS:
            rng = None
            m = re.search(r"(\d+),(\d+)p", first)
            if toks[0] == "sed" and m:
                rng = (int(m.group(1)), int(m.group(2)))
            m = re.search(r"(?:-n\s*|-)(\d+)\b", first)
            if toks[0] == "head" and m:
                rng = (1, int(m.group(1)))
            for f in {f for f in (_rel(tree, cwd, t) for t in toks[1:]) if f}:
                n = _nlines(tree, f)
                if toks[0] == "tail" and m:
                    rng = (max(1, n - int(m.group(1)) + 1), n)
                s, e = rng or (1, n)
                view.setdefault(f, []).append((max(1, s), min(n, max(s, e))))
    for line in text.split("\n"):
        m = re.match(r"^(\d+)[:-]", line)
        if m and len(set(single)) == 1:
            view.setdefault(single[0], []).append((int(m.group(1)),) * 2)
            continue
        for m in re.finditer(r"([:-])(\d+)\1", line):
            prefix = line[:m.start()]
            f = next((r for r in (_rel(tree, c, prefix) for c in reversed(cwds)) if r), "")
            if f:
                view.setdefault(f, []).append((int(m.group(2)),) * 2)
                break
    return {f: treesit.merge(v) for f, v in view.items()}


def _ce_rows(obj, rows):
    if isinstance(obj, dict):
        p = next((obj[k] for k in _PATH_KEYS if isinstance(obj.get(k), str)), None)
        s = obj.get("start_line") or obj.get("startLine")
        if p and type(s) is int:
            code = next((obj[k] for k in _CODE_KEYS if isinstance(obj.get(k), str) and obj[k].strip()), "")
            rows.append({"path": p, "start": s, "end": obj.get("end_line") or obj.get("endLine"), "code": code})
            return rows
        for v in obj.values():
            _ce_rows(v, rows)
    elif isinstance(obj, list):
        for v in obj:
            _ce_rows(v, rows)
    return rows


def ce_view(tree, text, collection=None):
    try:
        obj = json.loads(text)
    except ValueError:
        try:
            obj = toon.loads(text)
        except (ValueError, IndexError, AttributeError):
            return {}, [], False
    got = obj.get("collection") if isinstance(obj, dict) else None
    if collection and got and got != collection:
        return {}, [], True
    view, rows = {}, []
    for r in _ce_rows(obj, []):
        f = treesit.resolve(tree, r["path"])
        rows.append(f or r["path"])
        if f and r["code"]:
            end = r["end"] if type(r["end"]) is int else None
            got_lines = verified_lines(os.path.join(tree, f), r["code"], (r["start"], end))
            if got_lines:
                view.setdefault(f, []).extend(got_lines)
    return {f: treesit.merge(v) for f, v in view.items()}, rows, False


def hook_view(tree, ctx):
    """Arm-H hits: `- path:start-end symbol | first line`; credit the line whose text matches the file."""
    view = {}
    for m in re.finditer(r"(?m)^- (\S+?):(\d+)-(\d+) .*?\| (.*)$", ctx or ""):
        f = treesit.resolve(tree, m.group(1))
        first = m.group(4).strip()
        if not f or not first:
            continue
        lines = open(os.path.join(tree, f), encoding="utf-8", errors="replace").read().split("\n")
        for ln in range(int(m.group(2)), min(int(m.group(3)), len(lines)) + 1):
            if lines[ln - 1].strip() == first:
                view.setdefault(f, []).append((ln, ln))
                break
    return {f: treesit.merge(v) for f, v in view.items()}


def gold_regions(task, pad=3):
    out = {}
    for f in task["gold_files"]:
        iv = [(max(1, s - pad), e + pad) for s, e in task["gold_changed"][f]]
        iv += [(d[2], d[3]) for d in task["gold_defs"].get(f, [])]
        out[f] = treesit.merge(iv)
    return out


def parse_answer(text):
    if not text or "ANSWER:" not in text:
        return None
    tail = text[text.rfind("ANSWER:") + 7:]
    start = tail.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(tail[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(tail[start:i + 1])
                except ValueError:
                    return None
    return None


def answer_metrics(task, tree, answer):
    ans = answer if isinstance(answer, dict) else {}
    files, spans = [], {}
    for f in ans.get("files") or []:
        r = treesit.resolve(tree, f) if isinstance(f, str) else ""
        if r and r not in files:
            files.append(r)
    for sp in ans.get("spans") or []:
        if not isinstance(sp, dict):
            continue
        f = treesit.resolve(tree, sp.get("file") or sp.get("path") or "")
        s, e = sp.get("start", sp.get("start_line")), sp.get("end", sp.get("end_line"))
        if f and isinstance(s, int):
            e = e if isinstance(e, int) else s
            spans.setdefault(f, []).append((min(s, e), max(s, e)))
            if f not in files:
                files.append(f)
    G = set(task["gold_files"])
    hit = len(G & set(files))
    hunks = {f: [(max(1, s - 3), e + 3) for s, e in task["gold_changed"][f]] for f in G}
    regions = gold_regions(task)
    return {"answer_parsed": answer is not None, "answer_files": files,
            "answer_recall": hit / len(G), "answer_precision": hit / len(files) if files else 0.0,
            "answer_file_hit1": bool(files) and files[0] in G,
            "hunk_hit": any(treesit.inter(spans.get(f, []), hunks[f]) for f in G),
            "fn_hit": any(treesit.inter(spans.get(f, []), regions[f]) for f in G)}


def score(task, tree, stream_path, hook_log=None, collection=None):
    calls, seen_ids, uses, steps = [], {}, {}, []
    hooks = {}
    if hook_log and os.path.exists(hook_log):
        for line in open(hook_log):
            try:
                h = json.loads(line)
            except ValueError:
                continue
            hooks.setdefault(h.get("tool_use_id"), []).append(h)
    result, last_text = None, ""
    for raw in open(stream_path):
        try:
            ev = json.loads(raw)["ev"]
        except (ValueError, KeyError):
            continue
        typ = ev.get("type")
        if typ == "assistant":
            msg = ev.get("message", {})
            mid = msg.get("id")
            u = msg.get("usage") or {}
            if mid not in seen_ids:
                seen_ids[mid] = len(calls)
                calls.append({"prompt": sum(u.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens",
                                                                     "cache_read_input_tokens")),
                              "out": u.get("output_tokens") or 0})
            else:
                c = calls[seen_ids[mid]]
                c["out"] = max(c["out"], u.get("output_tokens") or 0)
            for b in msg.get("content", []):
                if b.get("type") == "tool_use":
                    uses[b["id"]] = (b["name"], b.get("input") or {}, seen_ids[mid])
                elif b.get("type") == "text":
                    last_text = b.get("text", "")
        elif typ == "user" and isinstance(ev.get("message", {}).get("content"), list):
            results = [b for b in ev["message"]["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
            tur = ev.get("tool_use_result") if len(results) == 1 else None
            for b in results:
                name, inp, ci = uses.get(b.get("tool_use_id"), ("?", {}, len(calls) - 1))
                text = _text(b.get("content"))
                view, misrouted = {}, False
                if not b.get("is_error"):
                    if name == "Read":
                        view = read_view(tree, inp, tur, text)
                    elif name == "Grep":
                        view = grep_view(tree, inp, text)
                    elif name == "Bash":
                        view = bash_view(tree, inp, text)
                    elif name.startswith(CE_PREFIX):
                        view, _rows, misrouted = ce_view(tree, text, collection)
                for h in hooks.get(b.get("tool_use_id"), []):
                    for f, iv in hook_view(tree, h.get("context")).items():
                        view[f] = treesit.merge(view.get(f, []) + iv)
                steps.append({"tool": name, "call": ci, "view": view, "chars": len(text), "misrouted": misrouted,
                              "hook": bool(hooks.get(b.get("tool_use_id")))})
        elif typ == "result":
            result = ev
    regions = gold_regions(task)
    G = set(task["gold_files"])

    def first(pred):
        for s in steps:
            if pred(s["view"]):
                return s
        return None

    out = {"api_calls": len(calls), "steps": len(steps)}
    for level, pred in (("file", lambda v: bool(G & set(v))),
                        ("region", lambda v: any(treesit.inter(v.get(f, []), regions[f]) for f in G))):
        s = first(pred)
        sfx = "" if level == "file" else "_region"
        if s is None:
            out.update({f"found{sfx}": False, f"ttg{sfx}": None, f"turns_to_gold{sfx}": None,
                        f"ttg_billed{sfx}": None, f"gold_tool{sfx}": None})
            continue
        k = s["call"]
        nxt = calls[k + 1]["prompt"] if k + 1 < len(calls) else calls[k]["prompt"] + calls[k]["out"] + s["chars"] // 4
        out.update({f"found{sfx}": True, f"ttg{sfx}": max(0, nxt - calls[0]["prompt"]),
                    f"turns_to_gold{sfx}": k + 1,
                    f"ttg_billed{sfx}": sum(c["prompt"] + c["out"] for c in calls[:k + 1]),
                    f"gold_tool{sfx}": "hook" if s["hook"] and not s["tool"].startswith(CE_PREFIX) else s["tool"]})
    for b in BUDGETS:
        out[f"found@{b // 1000}k"] = out["ttg"] is not None and out["ttg"] <= b
    res_text = (result or {}).get("result") or last_text
    out.update(answer_metrics(task, tree, parse_answer(res_text)))
    out["first_prompt_tokens"] = calls[0]["prompt"] if calls else None
    out["ce_misrouted"] = any(s["misrouted"] for s in steps)
    out["hook_calls"] = sum(len(v) for v in hooks.values())
    out["hook_hits"] = sum(1 for v in hooks.values() for h in v if h.get("context"))
    out["hook_errors"] = sum(1 for v in hooks.values() for h in v if h.get("error"))
    return out


def main():
    tasks = {t["id"]: t for t in llcore.load_jsonl(llcore.TASKS)}
    colls = llcore.load_json(llcore.COLLECTIONS)
    for p in sys.argv[1:]:
        rec = json.load(open(p))
        t = tasks[rec["id"]]
        base = p[:-5]
        rec["score"] = score(t, llcore.tree_dir(t["repo"], t["snapshot"]), base + ".jsonl",
                             base + ".hook.jsonl", colls.get(t["tree"], {}).get("collection"))
        llcore.save_json(p, rec)
        print(os.path.basename(p), json.dumps({k: rec["score"][k] for k in ("found", "ttg", "turns_to_gold", "answer_recall")}))


if __name__ == "__main__":
    main()
