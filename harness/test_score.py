#!/usr/bin/env python3
"""Self-checks for score.py on synthetic traces: python3 harness/test_score.py"""
import json
import os
import tempfile

import score

TASK = {"gold_files": ["src/a.go"], "gold_changed": {"src/a.go": [[5, 5]]}, "gold_defs": {"src/a.go": []}}


def tree():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "src"))
    open(os.path.join(d, "src/a.go"), "w").write("".join(f"line{i}\n" for i in range(1, 21)))
    open(os.path.join(d, "src/b.go"), "w").write("other\n")
    return d


def trace(d, steps, answer):
    """steps: [(tool_name, input, result_text, tool_use_result)] -> stream file; prompt grows 1000/call."""
    p = os.path.join(d, "t.jsonl")
    with open(p, "w") as fh:
        for i, (name, inp, text, tur) in enumerate(steps):
            msg = {"id": f"m{i}", "usage": {"input_tokens": 10_000 + 1000 * i, "output_tokens": 50},
                   "content": [{"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}]}
            fh.write(json.dumps({"ev": {"type": "assistant", "message": msg}}) + "\n")
            user = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": text}]}}
            if tur:
                user["tool_use_result"] = tur
            fh.write(json.dumps({"ev": user}) + "\n")
        n = len(steps)
        fin = {"id": f"m{n}", "usage": {"input_tokens": 10_000 + 1000 * n, "output_tokens": 50},
               "content": [{"type": "text", "text": "ANSWER: " + json.dumps(answer)}]}
        fh.write(json.dumps({"ev": {"type": "assistant", "message": fin}}) + "\n")
        fh.write(json.dumps({"ev": {"type": "result", "result": "ANSWER: " + json.dumps(answer)}}) + "\n")
    return p


def main():
    d = tree()
    # Grep in files mode shows a path only: no credit; then a Read of the gold file at call 2 is the reveal
    p = trace(d, [("Grep", {"pattern": "line5"}, "src/a.go", None),
                  ("Read", {"file_path": os.path.join(d, "src/a.go")}, "     1→line1",
                   {"file": {"filePath": os.path.join(d, "src/a.go"), "startLine": 1, "numLines": 20}})],
              {"files": ["src/a.go", "src/b.go"], "spans": [{"file": "src/a.go", "start": 4, "end": 6}]})
    s = score.score(TASK, d, p)
    assert s["found"] and s["turns_to_gold"] == 2 and s["ttg"] == 2000, s
    assert s["answer_recall"] == 1.0 and s["answer_precision"] == 0.5 and s["hunk_hit"], s
    # a CE row whose code matches the tree reveals gold; a mismatching one does not
    bad = json.dumps({"results": [{"path": "src/a.go", "start_line": 5, "end_line": 5, "snippet": "nope"}]})
    good = json.dumps({"results": [{"path": "src/a.go", "start_line": 5, "end_line": 5, "snippet": "line5"}]})
    p = trace(d, [("mcp__context-engine__search", {}, bad, None), ("mcp__context-engine__search", {}, good, None)], {})
    s = score.score(TASK, d, p)
    assert s["turns_to_gold"] == 2 and s["found_region"] and s["answer_parsed"], s
    # Bash grep -n output reveals the line
    p = trace(d, [("Bash", {"command": "grep -rn line5 ."}, "./src/a.go:5:line5", None)], {"files": []})
    assert score.score(TASK, d, p)["turns_to_gold"] == 1
    # cd + sed/grep on a relative path (Sonnet's Bash style)
    p = trace(d, [("Bash", {"command": "cd src; sed -n 3,6p a.go; grep -n line5 b.go"}, "line3\nline4", None)], {})
    s = score.score(TASK, d, p)
    assert s["turns_to_gold"] == 1 and s["found_region"], s
    p = trace(d, [("Bash", {"command": f"cd {d}/src && grep -rn line5 ."}, "./a.go:5:line5", None)], {})
    assert score.score(TASK, d, p)["turns_to_gold"] == 1
    p = trace(d, [("Bash", {"command": "cd src; grep -n line a.go"}, "5:line5", None)], {})
    assert score.score(TASK, d, p)["found_region"]
    p = trace(d, [("Bash", {"command": "ls src"}, "a.go\nb.go", None)], {})
    assert not score.score(TASK, d, p)["found"]
    # arm-H hook context attached to a tool use counts when its first line matches the file
    p = trace(d, [("Grep", {"pattern": "x"}, "No files found", None)], {"files": ["src/a.go"]})
    hl = os.path.join(d, "h.jsonl")
    open(hl, "w").write(json.dumps({"tool_use_id": "t0", "context": "hits:\n- src/a.go:3-9 f | line5"}) + "\n")
    s = score.score(TASK, d, p, hl)
    assert s["found"] and s["gold_tool"] == "hook" and s["hook_hits"] == 1, s
    print("test_score: ok")


if __name__ == "__main__":
    main()
