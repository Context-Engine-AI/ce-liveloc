// Count the files `ctxce index` would upload for a folder: the bridge's own ignore rules and file-type
// filter (imported, not copied), walked the way its scanWorkspace() walks (non-empty, < 10 MB).
// Usage: node count_indexable.mjs <bridge-repo> <folder> [--list]   -> prints the count (or a JSON list of paths)
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [bridge, root, mode] = process.argv.slice(2);
const { loadGitignore, isCodeFile } = await import(pathToFileURL(path.join(bridge, "src", "uploader.js")).href);
const MAX_FILE_SIZE = 10 * 1024 * 1024; // uploader.js MAX_FILE_SIZE
const ig = loadGitignore(root);
const files = [];
(function walk(dir, rel) {
  let entries;
  try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
  for (const e of entries) {
    const r = rel ? `${rel}/${e.name}` : e.name;
    if (ig.ignores(r) || ig.ignores(r + "/")) continue;
    const full = path.join(dir, e.name);
    if (e.isDirectory()) walk(full, r);
    else if (e.isFile() && isCodeFile(e.name)) {
      const size = fs.statSync(full).size;
      if (size > 0 && size < MAX_FILE_SIZE) files.push(r);
    }
  }
})(root, "");
console.log(mode === "--list" ? JSON.stringify(files) : files.length);
