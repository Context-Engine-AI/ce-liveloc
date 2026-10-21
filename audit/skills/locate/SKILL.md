---
name: context-engine
description: Hybrid semantic/lexical code search with a symbol graph over the indexed workspace. Use it to locate where behavior lives and who calls what, then read the located code.
---

# Context Engine: locate, then read

Context Engine (CE) tells you where to look. Reading the code is still your job.

1. Locate first. Start an investigation with one `repo_search` call that sets `include_snippet=false` and `limit=12`, with 2-3 phrasings in `queries=[...]`. You get ranked refs: path, line range, symbol, and `symbol_start_line`.
2. Read what CE located. Open the top refs with Read (the ref's line range, a little wider) before you conclude anything. Read every function you will cite.
3. Enumerate with the right tool. For "every place that does X", treat the refs as starting points, then Grep the key identifiers they reveal to find the rest. Use `symbol_graph` for callers of a function you found.
4. Take line numbers from Read or `grep -n` output.
5. Search CE again only when you learn a new term.
