"""Shared pieces of the GrapeRoot-Gitea audit A/B (clean-room; reuses harness/llcore.py).

The prompt set is GrapeRoot's published Gitea prompts, read at runtime from a pinned clone and never copied here:
  https://github.com/kunal12203/graperoot-benchmarks @ GRAPEROOT_SHA, prompts/gitea_go_30.json
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "harness"))
import llcore  # noqa: E402

GRAPEROOT_URL = "https://github.com/kunal12203/graperoot-benchmarks"
GRAPEROOT_SHA = "cae330f3db8588acb7ae8a22bb3f019a1b4a153a"
GRAPEROOT_PROMPTS = "prompts/gitea_go_30.json"
GRAPEROOT_DIR = os.environ.get("AUDIT_GRAPEROOT", "/tmp/graperoot-eval/repo")

REPO = "go-gitea/gitea"
SNAPSHOT = llcore.load_json(llcore.REPOS)[REPO]["snapshot"]
TREE_NAME = llcore.tree_name(REPO, SNAPSHOT)
TREE = llcore.tree_dir(REPO, SNAPSHOT)
# kept apart from work/runs so run.py's spend/stats never see these; AUDIT_TAG=smoke selects the smoke batch's dirs
_TAG = os.environ.get("AUDIT_TAG", "")
RUNS = os.path.join(llcore.WORK, "audit-runs" + (f"-{_TAG}" if _TAG else ""))
JUDGE = os.path.join(llcore.WORK, "audit-judge" + (f"-{_TAG}" if _TAG else ""))

MODELS = {"sonnet55": "us.anthropic.claude-sonnet-5-5", "haiku55": "us.anthropic.claude-haiku-5-5"}
# pinned Claude Code binary: the installed `claude` auto-updated 2.1.304 -> 2.1.306 mid-batch (11:40Z), so later runs
# call the 2.1.304 build directly; a run recorded on another version is invalid and reruns
CLAUDE_VERSION = os.environ.get("AUDIT_CLAUDE_VERSION", "2.1.304")
CLAUDE_BIN = os.path.expanduser(f"~/.local/share/claude/versions/{CLAUDE_VERSION}")
JUDGE_OPUS = "us.anthropic.claude-opus-5-5"
# both arms: the GrapeRoot prompt verbatim + this one line
CITE_LINE = "Cite each finding as path:line (path relative to the repository root)."
SMOKE_IDS = [1, 4, 7, 10, 13, 16, 19, 22, 25, 28]  # every third prompt: spans all four categories

# $/MTok: (input, 5m cache write, 1h cache write, cache read, output). Anthropic list prices from
# platform.claude.com/docs/en/about-claude/pricing (fetched 2026-10-20), times GEO.
RATES = {
    "us.anthropic.claude-sonnet-5-5": (2.00, 2.50, 4.00, 0.10, 10.00),
    "us.anthropic.claude-haiku-5-5": (0.10, 0.125, 0.20, 0.01, 0.50),  # prompt <= 100k tokens
    "us.anthropic.claude-haiku-5-5>100k": (0.50, 0.625, 1.00, 0.05, 2.50),
    "us.anthropic.claude-opus-5-5": (4.00, 5.00, 8.00, 0.20, 20.00),
}
GEO = 1.1
RATES_NOTE = ("Bedrock list = Anthropic list (platform.claude.com/docs/en/about-claude/pricing, fetched 2026-10-20) x 1.1: "
              "that page states Bedrock regional/multi-region endpoints carry a 10% premium over global for Claude 4.5+, "
              "and the us.anthropic.* IDs are US geo cross-region profiles. The Claude rows of aws.amazon.com/bedrock/pricing "
              "did not render, so the Bedrock page itself was not the source. Haiku 5.5 uses its >100k-prompt card per call.")


def load_prompts():
    head = subprocess.run(["git", "-C", GRAPEROOT_DIR, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if head != GRAPEROOT_SHA:
        raise SystemExit(f"{GRAPEROOT_DIR} is at {head or '?'}, expected pinned {GRAPEROOT_SHA}")
    items = json.load(open(os.path.join(GRAPEROOT_DIR, GRAPEROOT_PROMPTS)))
    return {int(x["id"]): x for x in items}


def prompt_text(item):
    return item["prompt"].strip() + "\n\n" + CITE_LINE


def collection():
    return llcore.load_json(llcore.COLLECTIONS)[TREE_NAME]["collection"]


def _cost(rates, inp, w5, w1, read, out):
    i, a, b, r, o = rates
    return (inp * i + w5 * a + w1 * b + read * r + out * o) * GEO / 1e6


def run_cost(events, main_model):
    """Dollars at RATES for one stream-json trace (main model per API call; other models from modelUsage).

    Input-side tokens come per call from assistant events (deduped by message id, last event wins); streamed
    output counts there are partial, so output comes from the result totals.
    """
    results = [e for e in events if e.get("type") == "result"]
    res = results[-1] if results else {}
    calls = {}
    for e in events:
        if e.get("type") == "assistant":
            m = e.get("message") or {}
            calls[m.get("id")] = m.get("usage") or {}
    usage, mu = res.get("usage") or {}, res.get("modelUsage") or {}
    if not res:  # killed before a result event: the last streamed usage per call is all there is
        usage = {k: sum(u.get(k) or 0 for u in calls.values()) for k in
                 ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")}
    cc = usage.get("cache_creation") or {}
    cw = usage.get("cache_creation_input_tokens") or 0
    f1h = (cc.get("ephemeral_1h_input_tokens") or 0) / cw if cw else 0.0
    total, over = 0.0, 0
    for u in calls.values():
        inp, w, rd = (u.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        big = main_model.endswith("haiku-5-5") and inp + w + rd > 100_000
        over += big
        total += _cost(RATES[main_model + (">100k" if big else "")], inp, w * (1 - f1h), w * f1h, rd, 0)
    # a background task finishing after the answer adds a result segment; segment usages add up
    out = sum((r.get("usage") or {}).get("output_tokens") or 0 for r in results) if results else usage.get("output_tokens") or 0
    # shortcut: output per call is not in the trace, so it is split over calls in proportion to the 100k tiers
    frac_big = over / len(calls) if calls else 0.0
    total += _cost(RATES[main_model], 0, 0, 0, 0, out * (1 - frac_big))
    if frac_big:
        total += _cost(RATES[main_model + ">100k"], 0, 0, 0, 0, out * frac_big)
    for m, x in mu.items():  # auxiliary models Claude Code may call (none expected with these flags)
        if m != main_model:
            total += _cost(RATES.get(m, RATES[main_model]), x.get("inputTokens") or 0, x.get("cacheCreationInputTokens") or 0,
                           0, x.get("cacheReadInputTokens") or 0, x.get("outputTokens") or 0)
    return round(total, 6), over


def spent():
    """Recomputed dollars over every agent run and Opus judgment so far (the $ cap counts both)."""
    tot = 0.0
    for d in sorted(os.path.join(llcore.WORK, x) for x in os.listdir(llcore.WORK) if x.startswith("audit-")):
        if not os.path.isdir(d):  # every batch, set-aside and aborted run was paid for
            continue
        for f in os.listdir(d):
            if f.endswith(".json"):
                try:
                    tot += json.load(open(os.path.join(d, f))).get("cost_bedrock") or 0.0
                except (ValueError, OSError):
                    pass
    return tot


def tree_manifest(tree=TREE):
    """Digest of (path, size, mtime) over the tree: the agents and the judge must all see the same snapshot."""
    import hashlib
    h = hashlib.sha256()
    for root, dirs, files in os.walk(tree):
        dirs.sort()
        for f in sorted(files):
            st = os.stat(os.path.join(root, f))
            h.update(f"{os.path.relpath(os.path.join(root, f), tree)}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()[:16]


def ce_server_info(cwd=TREE, timeout_s=60):
    """The CE MCP server's initialize result (serverInfo + instructions) as Claude Code receives it via the proxy."""
    import threading
    p = subprocess.Popen(["node", llcore.PROXY, llcore.INDEXER_URL], cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, bufsize=1)
    timer = threading.Timer(timeout_s, p.kill)
    timer.start()
    try:
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "audit", "version": "1"}}}) + "\n")
        for line in p.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == 1:
                return msg.get("result") or {"error": msg.get("error")}
        return {}
    finally:
        timer.cancel()
        p.terminate()
