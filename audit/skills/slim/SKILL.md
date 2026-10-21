---
name: context-engine
description: Hybrid semantic/lexical code search with a symbol graph over the indexed workspace. Use to find where behavior lives, who calls what, impact and call chains, and cited explanations across files and repos.
---

# Context Engine

Context Engine (CE) finds candidates fast. Your own file reads confirm them.

## Find
- `search`, or `repo_search`: ask by behavior, symptom or identifier. Put 2-3 phrasings in one call (`queries=[...]`) instead of calling repeatedly.
- `symbol_graph`: direct callers, callees, definitions and importers.
- `graph_query`: transitive impact, dependencies, cycles, and `call_chain` from A to B.
  - Rows labelled `name_match` are same-name guesses. Confirm them before relying on them.
- `context_answer`: a cited explanation of how something works across several files.
- `cross_repo_search`: code that may live in another indexed repo, such as client and server or shared libraries.

## Verify before you assert
CE results are leads, not proof.
- Before you claim code is safe, unsafe, missing, unused or dead, Read the enclosing function and the caller that guards it.
- A ref without code has not been shown to you. Open it before citing it.
- Take line numbers from Read or `grep -n` output. Never count lines inside a snippet. A ref's `symbol_start_line` is where its symbol starts.
- Grep and Read stay the right tools for exact literals, counts, files outside the index, and anything you cite or edit.

## If results look wrong
- Empty results, or results from the wrong repo: run `qdrant_list`, then `set_session_defaults(collection="<name>")`.
- Missing recent changes: check `qdrant_status`.
