#!/usr/bin/env python3
"""Self-checks for cites.py and the cost recompute on synthetic data: python3 audit/test_cites.py"""
import os
import tempfile

import acore
import cites


def tree():
    d = tempfile.mkdtemp()
    for rel, n in (("routers/api/v1/api.go", 50), ("models/user.go", 10), ("a/dup.go", 5), ("b/dup.go", 30),
                   ("templates/base.tmpl", 3), ("services/x.go", 60), ("lib/big.go", 1000), (".golangci.yml", 5)):
        os.makedirs(os.path.join(d, os.path.dirname(rel)), exist_ok=True)
        open(os.path.join(d, rel), "w").write("".join(f"line{i}\n" for i in range(1, n + 1)))
    return d


def main():
    d = tree()
    text = ("Missing auth at `routers/api/v1/api.go:12` and routers/api/v1/api.go:40-45; see also "
            "[user](models/user.go#L3) and ./models/user.go:L9 and models/user.go:11 (past EOF), "
            "routers/api/v1/nope.go:1 (no file), dup.go:20 (ambiguous, fits b only), "
            "dup.go:99 (ambiguous, fits none), templates/base.tmpl:2:7 (line:col), "
            "routers/api/v1/api.go:40-45 again, http://example.com:8080 and localhost:3000 are not citations, "
            f"{d}/models/user.go:2 is an absolute path, https://github.com/x/y/blob/main/models/user.go#L4 is a URL.\n"
            "Partial path v1/api.go:7 and bare api.go:8 resolve uniquely; b/dup.go:21 then makes dup.go:22 resolve.\n"
            "| `models/user.go:4`, `:5` | checked (`:6`, `:7-8`) |\n"
            "  - `:9` and `:10` belong to user.go too; `:3000` (a port, past EOF) is dropped.\n"
            "`routers/api/v1/api.go:30-31`, `33` and `35` chain.\n"
            "`routers/api/v1/api.go:41`. In `services/x.go` (`:42`) the anchor moved; cfg.Key and ctx.JSON are not files.\n"
            "`lib/big.go:900`, `404` and `403` are status codes; `lib/big.go:910`, `950` chain; buf[:64] is a slice.\n"
            "Root dotfile .golangci.yml:2 exists. `lib/big.go:700` then a/dup.go:3, b/dup.go:4, and later bare dup.go:25"
            " fits only b/dup.go even though a/dup.go was cited last.")
    r = cites.check(text, d)
    got = {(raw, a, b): (st, p) for raw, a, b, st, p in r["rows"]}
    want = {
        ("routers/api/v1/api.go", 12, None): ("ok", "routers/api/v1/api.go"),
        ("routers/api/v1/api.go", 40, 45): ("ok", "routers/api/v1/api.go"),
        ("models/user.go", 3, None): ("ok", "models/user.go"),
        ("./models/user.go", 9, None): ("ok", "models/user.go"),
        ("models/user.go", 11, None): ("bad_line", "models/user.go"),
        ("routers/api/v1/nope.go", 1, None): ("bad_path", ""),
        ("dup.go", 20, None): ("ambiguous", ""),
        ("dup.go", 99, None): ("bad_line", ""),
        ("templates/base.tmpl", 2, None): ("ok", "templates/base.tmpl"),
        (f"{d}/models/user.go", 2, None): ("ok", "models/user.go"),
        ("v1/api.go", 7, None): ("ok", "routers/api/v1/api.go"),
        ("api.go", 8, None): ("ok", "routers/api/v1/api.go"),
        ("b/dup.go", 21, None): ("ok", "b/dup.go"),
        ("dup.go", 22, None): ("ok", "b/dup.go"),
        ("models/user.go", 4, None): ("ok", "models/user.go"),
        ("models/user.go", 5, None): ("ok", "models/user.go"),
        ("models/user.go", 6, None): ("ok", "models/user.go"),
        ("models/user.go", 7, 8): ("ok", "models/user.go"),
        ("models/user.go", 10, None): ("ok", "models/user.go"),
        ("routers/api/v1/api.go", 30, 31): ("ok", "routers/api/v1/api.go"),
        ("routers/api/v1/api.go", 33, None): ("ok", "routers/api/v1/api.go"),
        ("routers/api/v1/api.go", 35, None): ("ok", "routers/api/v1/api.go"),
        ("routers/api/v1/api.go", 41, None): ("ok", "routers/api/v1/api.go"),
        ("services/x.go", 42, None): ("ok", "services/x.go"),
        ("lib/big.go", 900, None): ("ok", "lib/big.go"),
        ("lib/big.go", 910, None): ("ok", "lib/big.go"),
        ("lib/big.go", 950, None): ("ok", "lib/big.go"),
        (".golangci.yml", 2, None): ("ok", ".golangci.yml"),
        ("lib/big.go", 700, None): ("ok", "lib/big.go"),
        ("a/dup.go", 3, None): ("ok", "a/dup.go"),
        ("b/dup.go", 4, None): ("ok", "b/dup.go"),
        ("dup.go", 25, None): ("ok", "b/dup.go"),
    }
    # `:9` repeats ./models/user.go:L9 (same resolved citation) so it is not a new row
    assert got == want, sorted(set(got.items()) ^ set(want.items()))
    assert (r["n"], r["ok"], r["hallucinated"], r["ambiguous"]) == (32, 28, 3, 1), r
    assert abs(r["halluc_rate"] - 3 / 31) < 1e-9 and abs(r["evidence_share"] - 28 / 32) < 1e-9
    assert cites.check("no citations here", d)["halluc_rate"] is None

    ev, dropped = cites.evidence(d, r["rows"], [], cap_chars=100_000)
    assert "--- models/user.go lines 1-10" in ev and "nope.go:1 (bad_path)" in ev and dropped == 0, ev[:400]

    # cost: one Sonnet call, 1000 input + 2000 5m-write + 3000 read + 400 output
    evs = [{"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 1000, "cache_creation_input_tokens": 2000,
                                                                   "cache_read_input_tokens": 3000, "output_tokens": 1}}},
           {"type": "result", "usage": {"input_tokens": 1000, "cache_creation_input_tokens": 2000, "cache_read_input_tokens": 3000,
                                        "output_tokens": 400, "cache_creation": {"ephemeral_5m_input_tokens": 2000}},
            "modelUsage": {"us.anthropic.claude-sonnet-5-5": {}}}]
    c, over = acore.run_cost(evs, "us.anthropic.claude-sonnet-5-5")
    assert abs(c - (1000 * 2 + 2000 * 2.5 + 3000 * 0.1 + 400 * 10) * 1.1 / 1e6) < 1e-9 and over == 0, c
    # a Haiku call over 100k prompt tokens uses the long-context card, output included
    evs[0]["message"]["usage"]["cache_read_input_tokens"] = evs[1]["usage"]["cache_read_input_tokens"] = 200_000
    c, over = acore.run_cost(evs, "us.anthropic.claude-haiku-5-5")
    assert over == 1 and abs(c - (1000 * .5 + 2000 * .625 + 200_000 * .05 + 400 * 2.5) * 1.1 / 1e6) < 1e-9, c
    print("cites + cost self-checks passed")


if __name__ == "__main__":
    main()
