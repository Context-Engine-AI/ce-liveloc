"""Deterministic `path:line` citation check against the pinned tree, plus the judge's code-evidence excerpts.

Recognised forms: path:N, path:N-M (also en/em dash), path:LN, path#LN, path#LN-LM; `:N:col` keeps N.
Follow-ups attach to the most recent path cited earlier in the answer: a standalone `:N` / `:N-M` anywhere after it
(e.g. "(`:65`, `:198`)" or a later line "`:291` and `:360`"), and backticked bare numbers chained right after a
citation ("`api.go:1421-1426`, `1429` and `1431`"). shortcut: a follow-up past the end of its file is dropped, not
counted as hallucinated, since ports and status codes look the same (`:3000`); a stricter parse would need context.
A path candidate counts only if its extension occurs in the tree (so host:port and URLs are not citations); a path
named without a line (and resolving in the tree) becomes the anchor for later follow-ups. A backticked bare number counts
only within FOLLOW_SPAN lines of the line it follows, and `[:N]` (a Go slice) is not a follow-up.
Resolution: the LiveLoc rule (tree prefix cut, then shorter suffixes); else a partial path (`v1/repo/repo.go`) or bare
file name resolves to the unique tree file ending with it, or, if several, to the one this answer already cited in
full; otherwise it is "ambiguous" (left out of the hallucination rate) if any candidate has the line.
Each distinct citation (resolved path, a, b) is one of: ok | bad_path | bad_line | ambiguous.
"""
import bisect
import functools
import os
import re

import acore  # noqa: F401  (puts harness/ on sys.path)
import treesit

_PATH = r"(?P<path>/?(?:[\w.@+-]+/)*[\w.@+-]*\w\.(?P<ext>[A-Za-z][A-Za-z0-9]{0,7}))"
_LINES = r"(?P<a>\d{1,6})(?:\s?[-–—]\s?L?(?P<b>\d{1,6}))?"
CITE = re.compile(r"(?<![\w/.:@-])" + _PATH + r"(?::L?|#L)" + _LINES)
# one scan: a path with lines | a path alone (moves the follow-up anchor) | a standalone :N (not a Go slice `[:64]`)
TOKEN = re.compile(r"(?<![\w/.:@-])" + _PATH + r"(?:(?::L?|#L)" + _LINES + r")?"
                   r"|(?<![\w/.:@\[-]):L?(?P<sa>\d{1,6})(?:\s?[-–—]\s?L?(?P<sb>\d{1,6}))?")
FOLLOW = re.compile(r"`?\s*(?:,|and|or|&)\s*`L?(?P<a>\d{1,6})(?:\s?[-–—]\s?L?(?P<b>\d{1,6}))?`")
FOLLOW_SPAN = 200  # a backticked bare number counts only this close to the line it follows (not `404` after :1438)


@functools.lru_cache(maxsize=8)
def tree_index(tree):
    """(extensions present, basename -> [repo paths], all repo paths) for the tree."""
    exts, names, paths = set(), {}, []
    for root, _dirs, files in os.walk(tree):
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), tree)
            exts.add(os.path.splitext(f)[1][1:].lower())
            names.setdefault(f, []).append(rel)
            paths.append(rel)
    return frozenset(exts), names, tuple(paths)


@functools.lru_cache(maxsize=20000)
def nlines(abs_path):
    try:
        return len(open(abs_path, "rb").read().splitlines())
    except OSError:
        return 0


def parse(text, tree=acore.TREE):
    """Tokens in order: ("cite", raw, a, b) | ("mention", raw) | ("short", a, b) | ("follow", a, b, anchor_line)."""
    exts = tree_index(tree)[0]
    text, out, pos = text or "", [], 0
    while (m := TOKEN.search(text, pos)):
        pos = m.end()
        if m.group("path"):
            if m.group("ext").lower() not in exts:
                continue
            if m.group("a") is None:
                out.append(("mention", m.group("path")))
                continue
            out.append(("cite", m.group("path"), int(m.group("a")), int(m.group("b")) if m.group("b") else None))
        else:
            out.append(("short", int(m.group("sa")), int(m.group("sb")) if m.group("sb") else None))
        anchor = out[-1][-2]
        while (f := FOLLOW.match(text, pos)):
            out.append(("follow", int(f.group("a")), int(f.group("b")) if f.group("b") else None, anchor))
            pos = f.end()
    return out


def resolve(tree, raw, cited, line=None):
    """-> (resolved path or '', candidates when ambiguous). `cited`: paths this answer resolved earlier; among those,
    the most recent one the line fits wins (so a later bare `api.go:969` follows `routers/api/v1/api.go:969`)."""
    p = treesit.resolve(tree, raw) if "/" in raw else ""
    if p:
        return p, []
    tail = re.sub(r"^(?:\.{0,2}/)+", "", raw.strip())  # ./x, ../x, /x -> x; keeps a dotfile's own dot
    _exts, names, paths = tree_index(tree)
    cands = names.get(tail, []) if "/" not in tail else [q for q in paths if q.endswith("/" + tail)]
    if len(cands) == 1:
        return cands[0], []
    mine = [q for q in reversed(cited) if q in cands]
    fits = [q for q in mine if line is None or line <= nlines(os.path.join(tree, q))]
    return ((fits or mine)[0], []) if mine else ("", cands)


def check(text, tree=acore.TREE):
    rows, seen, cited, last = [], set(), [], None  # last: (raw, resolved, candidates) of the anchor path
    for tok in parse(text, tree):
        if tok[0] == "mention":
            p, cands = resolve(tree, tok[1], cited)
            if p:  # only a mention that resolves moves the anchor (`cfg.Key` is not a file)
                cited.append(p)
                last = (tok[1], p, [])
            continue
        if tok[0] == "cite":
            raw, a, b = tok[1:]
            p, cands = resolve(tree, raw, cited, a)
            last = (raw, p, cands)
        else:
            if last is None or (tok[0] == "follow" and abs(tok[1] - tok[3]) > FOLLOW_SPAN):
                continue
            (raw, p, cands), (a, b) = last, tok[1:3]
        if p:
            n = nlines(os.path.join(tree, p))
            st = "ok" if 1 <= a <= n and (b is None or b < a or b <= n) else "bad_line"  # b < a: "120-35", judged on a
            cited.append(p)
        elif cands:
            st = "ambiguous" if any(1 <= a <= nlines(os.path.join(tree, c)) for c in cands) else "bad_line"
        else:
            st = "bad_path"
        if tok[0] != "cite" and st == "bad_line":
            continue
        k = (p or raw, a, b)
        if k not in seen:
            seen.add(k)
            rows.append((raw, a, b, st, p))
    cnt = {k: sum(1 for r in rows if r[3] == k) for k in ("ok", "bad_path", "bad_line", "ambiguous")}
    n, bad = len(rows), cnt["bad_path"] + cnt["bad_line"]
    return {"n": n, **cnt, "hallucinated": bad,
            "halluc_rate": bad / (n - cnt["ambiguous"]) if n - cnt["ambiguous"] else None,
            "evidence_share": cnt["ok"] / n if n else None,
            "files": len({r[4] for r in rows if r[4]}), "rows": rows}


# ---------------------------------------------------------------- judge evidence

def _def_window(tree, p, line, max_lines):
    """Line range of the smallest definition containing `line` if it is at most max_lines long."""
    ab = os.path.join(tree, p)
    offs, _ = treesit._line_offsets(ab)
    if not offs or not 0 < line <= len(offs):
        return None
    lb, best = offs[line - 1], None
    for _k, _n, ds, de in treesit.file_defs(ab):
        if ds <= lb < de:
            s, e = bisect.bisect_right(offs, ds), bisect.bisect_right(offs, max(ds, de - 1))
            if e - s + 1 <= max_lines and (best is None or e - s < best[1] - best[0]):
                best = (s, e)
    return best


def windows(tree, rows, ctx=8, max_def=60, max_span=60):
    out = []
    for _raw, a, b, status, p in rows:
        if status != "ok":
            continue
        end = b if b and b >= a else a
        end = min(end, a + max_span)
        w = _def_window(tree, p, a, max_def)
        s, e = (min(w[0], a), max(w[1], end)) if w else (a - ctx, end + ctx)
        out.append((p, max(1, s), min(nlines(os.path.join(tree, p)), e)))
    return out


def evidence(tree, rows_x, rows_y, cap_chars=100_000):
    """Pooled, answer-agnostic code evidence for both answers' citations, interleaved so a cap hits both alike."""
    wx, wy = windows(tree, rows_x), windows(tree, rows_y)
    order = [w for pair in zip(wx, wy) for w in pair] + wx[len(wy):] + wy[len(wx):]
    by_file, used, dropped = {}, 0, 0
    for p, s, e in order:
        cost = (e - s + 1) * 60
        if used + cost > cap_chars:
            dropped += 1
            continue
        used += cost
        by_file.setdefault(p, []).append((s, e))
    parts = []
    for p in sorted(by_file):
        src = open(os.path.join(tree, p), "rb").read().decode("utf-8", "replace").splitlines()
        for s, e in treesit.merge(by_file[p]):
            body = "\n".join(f"{i:>6}  {src[i - 1]}" for i in range(s, min(e, len(src)) + 1))
            parts.append(f"--- {p} lines {s}-{e}\n{body}")
    missing = sorted({f"{raw}:{a}" + (f"-{b}" if b else "") + f" ({st})"
                      for raw, a, b, st, _p in rows_x + rows_y if st in ("bad_path", "bad_line")})
    if missing:
        parts.append("--- citations that do not exist in the repository:\n" + "\n".join(missing))
    if dropped:
        parts.append(f"--- {dropped} further cited regions omitted (evidence size cap)")
    return "\n\n".join(parts) if parts else "(no citations)", dropped
