"""Shared pieces of the CE-LiveLoc harness.

Adapted from ce-benchmarks-contextbench/contextbench/cbcore.py (same proxy MCP client, workspace mapping,
disk guard, build-digest stamping). Nothing here reads or prints credentials: the CE bearer stays inside
the proxy, Bedrock auth is copied into the child env only.
"""
import hashlib
import json
import os
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORK = os.environ.get("LIVELOC_WORK", os.path.join(ROOT, "work"))
# agent trees live outside the repo so an agent poking at ".." cannot reach gold data
TREES = os.environ.get("LIVELOC_TREES", "/tmp/liveloc/trees")
DATA = os.path.join(ROOT, "data")
TASKS = os.path.join(DATA, "tasks.jsonl")
REPOS = os.path.join(DATA, "repos.json")
COLLECTIONS = os.path.join(WORK, "collections.json")
RUNS = os.path.join(WORK, "runs")
MCP_CFG = os.path.join(WORK, "ce-mcp.json")
NO_MCP_CFG = os.path.join(WORK, "no-mcp.json")
PROXY = os.path.expanduser("~/.ctxce/bin/ctx-mcp-proxy.js")
INDEXER_URL = "https://dev.context-engine.ai/indexer/mcp"
BRIDGE = os.path.expanduser("~/Desktop/pathful/Context-Engine-MCP-Bridge/bin/ctxce.js")
MIN_FREE_GIB = 25


def tree_name(repo, sha):
    """Folder name == CE collection base name (the bridge names a collection after its folder)."""
    return f"liveloc-{repo.replace('/', '__')}-{sha[:8]}"


def tree_dir(repo, sha):
    return os.path.join(TREES, tree_name(repo, sha))


def load_jsonl(path):
    return [json.loads(l) for l in open(path)] if os.path.exists(path) else []


def load_json(path, default=None):
    return json.load(open(path)) if os.path.exists(path) else ({} if default is None else default)


def save_json(path, obj):
    tmp = path + ".tmp"
    json.dump(obj, open(tmp, "w"), indent=1, sort_keys=True)
    os.replace(tmp, path)


def free_gib(path="/"):
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize / 2 ** 30


def need_disk(what, wait_s=None):
    """Refuse to start a clone/index/run when local free space is under the floor.

    With wait_s (or LIVELOC_DISK_WAIT_S), wait that long for space to come back before giving up: other
    agents' builds share this disk, so dips below the floor are usually temporary.
    """
    wait_s = float(os.environ.get("LIVELOC_DISK_WAIT_S", 0)) if wait_s is None else wait_s
    t0 = time.time()
    while (free := free_gib()) < MIN_FREE_GIB:
        if time.time() - t0 >= wait_s:
            raise SystemExit(f"disk guard: {free:.1f} GiB free < {MIN_FREE_GIB} GiB floor; not starting {what}")
        print(f"  disk guard: {free:.1f} GiB free < {MIN_FREE_GIB} GiB; waiting before {what}", flush=True)
        time.sleep(120)
    return free




def _workspace_dir(workspace_path):
    """Verbatim port of computeWorkspaceDir() in ~/.ctxce/bin/ctx-mcp-proxy.js."""
    normalized = os.path.abspath(workspace_path).replace("\\", "/").lower()
    h = hashlib.sha256(normalized.encode()).hexdigest()[:12]
    safe = "".join(c if (c.isalnum() and c.isascii()) or c in "_-" else "_" for c in os.path.basename(workspace_path))[:50]
    return os.path.join(os.path.expanduser("~/.ctxce/workspaces"), f"{safe}-{h}")


def map_workspace(tree, collection):
    """Make the proxy send X-Collection=<collection> for an agent whose cwd is `tree`.

    The forward lookup is the proxy's highest-priority source, so this wins over any
    process-tree hint left by an outer editor session. Writes both the /tmp alias and the
    /private/tmp realpath that the CLI reports as cwd.
    """
    for p in {tree, os.path.realpath(tree)}:
        ws = _workspace_dir(p)
        os.makedirs(ws, exist_ok=True)
        with open(os.path.join(ws, "ctx_config.json"), "w") as fh:
            json.dump({"default_collection": collection}, fh, indent=2)
        with open(os.path.join(ws, "meta.json"), "w") as fh:
            json.dump({"workspace_path": p}, fh, indent=2)


def write_mcp_configs():
    os.makedirs(WORK, exist_ok=True)
    cfg = {"mcpServers": {"context-engine": {"command": "node", "args": [PROXY, INDEXER_URL], "type": "stdio"}}}
    with open(MCP_CFG, "w") as fh:
        json.dump(cfg, fh)
    os.chmod(MCP_CFG, 0o600)
    with open(NO_MCP_CFG, "w") as fh:
        json.dump({"mcpServers": {}}, fh)




KUBE_CONTEXT = os.environ.get("LIVELOC_KUBE_CONTEXT", "")  # empty: the current kubectl context
KUBE_NS = os.environ.get("LIVELOC_KUBE_NS", "context-engine")


def indexer_digests():
    """Sorted unique image digests of the running mcp-indexer-http pods (read-only kubectl), or [] if unknown.

    One digest = one CE build serving every request; two = a rollout is in progress.
    """
    try:
        out = subprocess.run(["kubectl", *(["--context", KUBE_CONTEXT] if KUBE_CONTEXT else []), "-n", KUBE_NS, "get", "pods", "-l",
                              "app=context-engine,component=mcp-indexer-http", "--field-selector=status.phase=Running",
                              "-o", "jsonpath={range .items[*]}{.status.containerStatuses[*].imageID}{'\\n'}{end}",
                              "--request-timeout=20s"], capture_output=True, text=True, timeout=40)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return sorted({d.split("@")[-1] for line in out.stdout.split() for d in line.split() if "sha256:" in d})


# ---------------------------------------------------------------- MCP over the proxy (stdio)

class Mcp:
    """Minimal JSON-RPC client over the CE proxy. The bearer never leaves the proxy process."""

    def __init__(self, cwd, attempts=3):
        for i in range(attempts):  # the backend intermittently answers 5xx during the handshake
            self.p = subprocess.Popen(["node", PROXY, INDEXER_URL], cwd=cwd, stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
            self.n = 0
            try:
                self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "liveloc", "version": "1"}})
                break
            except RuntimeError:
                self.p.kill()
                if i == attempts - 1:
                    raise
                time.sleep(20)
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")

    def rpc(self, method, params):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}) + "\n")
        for line in self.p.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(str(msg["error"])[:300])
                return msg["result"]
        raise RuntimeError("proxy closed")

    def tool(self, name, args):
        res = self.rpc("tools/call", {"name": name, "arguments": args})
        if res.get("structuredContent"):
            return res["structuredContent"]
        text = "".join(c.get("text", "") for c in res.get("content", []))
        try:
            return json.loads(text)
        except ValueError:
            return {"ok": False, "raw": text[:300]}

    def close(self):
        self.p.terminate()


def bedrock_env():
    """Bedrock auth vars from ~/.claude/settings.json env (never printed)."""
    env = json.load(open(os.path.expanduser("~/.claude/settings.json"))).get("env", {})
    out = {}
    for k in ("CLAUDE_CODE_USE_BEDROCK", "AWS_REGION", "AWS_BEARER_TOKEN_BEDROCK"):
        v = os.environ.get(k) or env.get(k)
        if not v:
            raise SystemExit(f"{k} missing from env and ~/.claude/settings.json")
        out[k] = str(v)
    return out
