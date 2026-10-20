#!/usr/bin/env node
/**
 * CE-LiveLoc arm H: Claude Code PreToolUse hook on Grep/Glob/Bash(grep|rg).
 *
 * Extracts the search pattern the way ~/.claude/hooks/gitnexus/gitnexus-hook.cjs does, asks Context Engine
 * `search` for it (<= 3 s), and returns the top ~5 hits (path:line, symbol, signature; <= 1.5 KB) as
 * hookSpecificOutput.additionalContext. The agent never has to choose CE. Any failure = no output.
 *
 * Collection: the proxy's workspace mapping for the cwd (~/.ctxce/workspaces/<name>-<sha12>/ctx_config.json),
 * else LIVELOC_COLLECTION. Auth: the CE session in ~/.ctxce/auth.json, sent only as the request header.
 * Every call is appended to LIVELOC_HOOK_LOG (pattern, latency, hits; never credentials).
 */
const fs = require("fs");
const os = require("os");
const path = require("path");
const crypto = require("crypto");

const URL_ = process.env.LIVELOC_CE_URL || "https://dev.context-engine.ai/indexer/mcp";
const TIMEOUT_MS = 3000;
const MAX_CTX = 1500;
const TOP = 5;

function readInput() {
  try { return JSON.parse(fs.readFileSync(0, "utf-8")); } catch { return {}; }
}

// verbatim port of gitnexus-hook.cjs extractPattern()
function extractPattern(toolName, toolInput) {
  if (toolName === "Grep") return toolInput.pattern || null;
  if (toolName === "Glob") {
    const m = (toolInput.pattern || "").match(/[*\/]([a-zA-Z][a-zA-Z0-9_-]{2,})/);
    return m ? m[1] : null;
  }
  if (toolName === "Bash") {
    const cmd = toolInput.command || "";
    if (!/\brg\b|\bgrep\b/.test(cmd)) return null;
    const tokens = cmd.split(/\s+/);
    const flagsWithValues = new Set(["-e", "-f", "-m", "-A", "-B", "-C", "-g", "--glob", "-t", "--type", "--include", "--exclude"]);
    let foundCmd = false, skipNext = false;
    for (const token of tokens) {
      if (skipNext) { skipNext = false; continue; }
      if (!foundCmd) { if (/\brg$|\bgrep$/.test(token)) foundCmd = true; continue; }
      if (token.startsWith("-")) { if (flagsWithValues.has(token)) skipNext = true; continue; }
      const cleaned = token.replace(/['"]/g, "");
      return cleaned.length >= 3 ? cleaned : null;
    }
  }
  return null;
}

// regex syntax -> words, so a grep pattern becomes a usable CE query
function toQuery(pattern) {
  return pattern.replace(/\\[sSwWdDbB]/g, " ").replace(/\\(.)/g, "$1").replace(/[\^$.*+?()[\]{}|]/g, " ")
    .replace(/\s+/g, " ").trim();
}

function workspaceCollection(cwd) {
  const norm = path.resolve(cwd).replace(/\\/g, "/").toLowerCase();
  const hash = crypto.createHash("sha256").update(norm).digest("hex").slice(0, 12);
  const safe = path.basename(cwd).replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 50);
  try {
    const cfg = JSON.parse(fs.readFileSync(path.join(os.homedir(), ".ctxce", "workspaces", `${safe}-${hash}`, "ctx_config.json"), "utf8"));
    if (cfg && cfg.default_collection) return cfg.default_collection;
  } catch { /* fall through */ }
  return (process.env.LIVELOC_COLLECTION || "").trim();
}

function bearer() {
  try {
    const auth = JSON.parse(fs.readFileSync(path.join(os.homedir(), ".ctxce", "auth.json"), "utf8"));
    const u = new URL(URL_);
    const e = auth[`${u.protocol}//${u.host}`] || {};
    return e.sessionId || e.session_id || "";
  } catch { return ""; }
}

function format(query, rows) {
  const out = [`Context Engine hits for "${query}" (path:lines symbol | first line):`];
  let used = out[0].length;
  for (const r of rows.slice(0, TOP)) {
    const first = String(r.snippet || "").split("\n").map((l) => l.trim()).find((l) => l.length > 0) || "";
    let line = `- ${r.path}:${r.start_line}-${r.end_line || r.start_line} ${r.symbol || ""} | ${first}`.slice(0, 280);
    if (used + line.length + 1 > MAX_CTX) break;
    out.push(line);
    used += line.length + 1;
  }
  return out.length > 1 ? out.join("\n") : "";
}

function log(rec) {
  const p = process.env.LIVELOC_HOOK_LOG;
  if (p) { try { fs.appendFileSync(p, JSON.stringify({ t: Date.now() / 1000, ...rec }) + "\n"); } catch { /* ignore */ } }
}

async function main() {
  const input = readInput();
  if (input.hook_event_name !== "PreToolUse") return;
  const toolName = input.tool_name || "";
  if (!["Grep", "Glob", "Bash"].includes(toolName)) return;
  const pattern = extractPattern(toolName, input.tool_input || {});
  if (!pattern || pattern.length < 3) return;
  const query = toQuery(pattern);
  const base = { tool_use_id: input.tool_use_id, tool_name: toolName, pattern, query };
  if (query.length < 3) { log({ ...base, skipped: "short query" }); return; }
  const collection = workspaceCollection(input.cwd || process.cwd());
  const token = bearer();
  if (!collection || !token) { log({ ...base, skipped: "no collection or auth" }); return; }
  const t0 = Date.now();
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(URL_, {
      method: "POST", signal: ctl.signal,
      headers: { "content-type": "application/json", accept: "application/json, text/event-stream",
                 authorization: `Bearer ${token}`, "X-Collection": collection },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call",
                             params: { name: "search", arguments: { query, collection, limit: TOP } } }),
    });
    const body = await res.json();
    const rows = ((body.result || {}).structuredContent || {}).results || [];
    const ctx = format(query, rows);
    log({ ...base, ms: Date.now() - t0, status: res.status, n: rows.length, context: ctx });
    if (ctx) {
      process.stdout.write(JSON.stringify({ hookSpecificOutput: { hookEventName: "PreToolUse", additionalContext: ctx } }));
    }
  } catch (e) {
    log({ ...base, ms: Date.now() - t0, error: String((e && e.name) || e).slice(0, 80) });
  } finally {
    clearTimeout(timer);
  }
}

main();
