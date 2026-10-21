# CE vs no-CE on GrapeRoot's Gitea audit prompts (full: 30 prompts x 3 repeats, Sonnet 5.5 + Haiku 5.5)

- Prompts: https://github.com/kunal12203/graperoot-benchmarks @ `cae330f3db8588acb7ae8a22bb3f019a1b4a153a`, `prompts/gitea_go_30.json` (read at run time, not copied into this repo); prompt ids [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30].
- Code: go-gitea/gitea snapshot `4875bb3b2b4d9f2b1591b7b3b09d7517180fa7d4` (tree `liveloc-go-gitea__gitea-4875bb3b`, manifest `ccc9ad59ed87df79`); CE collection `admin_liveloc-go-gitea__gitea-4875bb3b-92106c05`.
- CE build (mcp-indexer-http image digest, pinned per arm): ce: `sha256:6f0ceb92656c5030cc217b80fae15df6bf8dbdbf5a973718009890792d7b4941`; ce-skill-shipped: `sha256:6f0ceb92656c5030cc217b80fae15df6bf8dbdbf5a973718009890792d7b4941`; ce-skill-slim: `sha256:c9f17e8d8d785df12182f7971294d01a887f3725ec5187bda96c14a5b874fe05`; ce-skill-stale: `sha256:6f0ceb92656c5030cc217b80fae15df6bf8dbdbf5a973718009890792d7b4941`.
- Agent: Claude Code 2.1.304, 2.1.306 headless. Judges: Claude Opus 5.5 (`us.anthropic.claude-opus-5-5`, Bedrock, `claude -p`, no tools) and GPT-6.1 (`gpt-6.1-sol`, `codex exec`, shell and web off).
- Dollars: Bedrock list = Anthropic list (platform.claude.com/docs/en/about-claude/pricing, fetched 2026-10-20) x 1.1: that page states Bedrock regional/multi-region endpoints carry a 10% premium over global for Claude 4.5+, and the us.anthropic.* IDs are US geo cross-region profiles. The Claude rows of aws.amazon.com/bedrock/pricing did not render, so the Bedrock page itself was not the source. Haiku 5.5 uses its >100k-prompt card per call. This report: agent runs $73.31, Opus judgments $64.51; all audit spend so far incl. set-aside runs $145.71. GPT-6.1 judge tokens are billed to a separate Azure deployment and not in these dollars (10,915,684 input / 1,938,347 output tokens).
- Arms: same prompt (GrapeRoot's text + one line asking for `path:line` citations); `--setting-sources ""` so no CLAUDE.md/AGENTS.md (the Gitea tree has both), user hooks or plugins; no stop, turn-budget or tool-use instructions; same built-in tools and deny list; identical silent ceilings (150 turns, $8, 45 min). `ce` adds only the CE MCP server, i.e. its tool definitions and the server's own initialize instructions (1426 chars, see `-ce-server.json`).
- Skill arms (added after the smoke run): `ce` + what the VS Code extension installs with `mcpClaudeSkillEnabled` (installSkillClaude): the skill folder copied verbatim plus the line "# Important, always use skill `context-engine`" prepended to CLAUDE.md, both in a fresh per-run `CLAUDE_CONFIG_DIR` loaded with `--setting-sources user`, plus the Skill tool; a run where Claude Code did not list the skill is invalid. `ce-skill-shipped`: SKILL.md sha256 `f64e9db1980d82fc…` from `Context-Engine-Extension@187f674`. `ce-skill-stale`: SKILL.md sha256 `4375a2180bee737c…` from `Context-Engine@24d0ff49`. `ce-skill-slim`: SKILL.md sha256 `637e438c7beda87f…` from `ce-liveloc@working copy, work/skills/slim`. **`ce-skill-shipped` is what the extension ships (Context-Engine-Extension 187f674, release 2.3.27). `ce-skill-stale` is an older copy from the retired monorepo, run by mistake first; it is reported as a finding about that file, not as the shipped product.** It has no opening frontmatter `---` and says "NEVER use Read File or grep for exploration". `ce-skill-shipped` ran on Sonnet only, after the other arms, so its wall time is not comparable; its tokens, cost and quality are paired with the same normal runs. `ce-skill-slim` (27 lines, written after a diagnosis of the shipped skill's losses) ran the same way, Sonnet only, later still, on its own frozen CE build; its wall time is not comparable either.
- Scoring: a hallucinated citation is a cited file missing from the pinned tree or a line past its end (`audit/cites.py`); the judges see both answers (tool mentions scrubbed) plus the pinned code at every cited location, pooled, and grade verified correctness, specificity and false claims with a rubric that gives volume no credit (`audit/judge.py`).

## sonnet55 (`us.anthropic.claude-sonnet-5-5`)

Complete sets (every arm present): 90 over 30 prompts. Valid runs outside a complete set (excluded from descriptives, still used in any pair they complete): none. Sets whose runs differ in Claude Code version or model: none.

| arm | runs | turns mean (median) | total tokens mean | input / cache-write / cache-read / output (mean) | $/run | $ total | wall s mean | tool calls/run | Read calls/run | CE calls/run | runs calling CE | Skill calls/run | runs at a ceiling | extra attempts | citations/run | cited regions found | hallucinated citations (pooled) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| normal | 90 | 14.0 (12) | 223,898 | 17 / 24,792 / 193,965 / 5,125 | 0.1459 | 13.13 | 49 | 13.0 | 3.77 | 0.00 | 0/90 | 0.00 | 0 | 0 | 35.3 | 0.993 | 2/3156 = 0.001 |
| ce | 90 | 13.3 (11) | 268,605 | 17 / 23,915 / 239,732 / 4,940 | 0.1465 | 13.19 | 53 | 12.3 | 3.11 | 0.31 | 27/90 | 0.00 | 0 | 0 | 35.6 | 0.985 | 2/3155 = 0.001 |
| ce-skill-shipped | 90 | 11.2 (10) | 330,546 | 15 / 42,146 / 284,557 / 3,829 | 0.1894 | 17.04 | 59 | 9.2 | 1.44 | 0.84 | 73/90 | 1.00 | 0 | 7 | 35.0 | 0.991 | 1/3124 = 0.000 |
| ce-skill-stale | 90 | 11.2 (10) | 223,236 | 15 / 28,351 / 191,159 / 3,710 | 0.1398 | 12.59 | 52 | 9.2 | 1.98 | 0.96 | 82/90 | 1.00 | 0 | 0 | 35.1 | 0.995 | 5/3144 = 0.002 |
| ce-skill-slim | 90 | 11.1 (10) | 203,861 | 16 / 22,180 / 177,933 / 3,731 | 0.1216 | 10.95 | 44 | 9.1 | 1.06 | 0.74 | 65/90 | 1.00 | 0 | 0 | 34.5 | 0.993 | 1/3081 = 0.000 |

Tool calls per run by tool (mean over complete sets):

| arm | Bash | Glob | Grep | Read | Skill | Write | ce:repo_search |
|---|---|---|---|---|---|---|---|
| normal | 5.08 | 0.08 | 4.01 | 3.77 | 0.00 | 0.02 | 0.00 |
| ce | 5.67 | 0.01 | 3.20 | 3.11 | 0.00 | 0.01 | 0.31 |
| ce-skill-shipped | 3.49 | 0.01 | 2.38 | 1.44 | 1.00 | 0.01 | 0.84 |
| ce-skill-stale | 2.77 | 0.00 | 2.48 | 1.98 | 1.00 | 0.01 | 0.96 |
| ce-skill-slim | 4.32 | 0.01 | 1.92 | 1.06 | 1.00 | 0.02 | 0.74 |

Blind pairwise judges, each treatment arm against normal on its own pairs (order balanced: Opus sees the treatment as report X in half the pairs per repeat, GPT sees every pair in the opposite order):

| judge | treatment | pairs | treatment wins | ties | normal wins | report-X wins (position check) | overall t / n | correctness t / n | false claims/answer t / n | verified/answer t / n |
|---|---|---|---|---|---|---|---|---|---|---|
| opus (us.anthropic.claude-opus-5-5) | ce | 90 | 43 | 1 | 46 | 40 | 7.07 / 7.14 | 7.63 / 7.90 | 0.59 / 0.46 | 10.81 / 9.93 |
| gpt (gpt-6.1-sol) | ce | 90 | 43 | 6 | 41 | 45 | 6.50 / 6.64 | 6.88 / 7.14 | 1.94 / 1.66 | 8.59 / 7.33 |

ce: the two judges pick the same winner in 64/90 pairs.
| opus (us.anthropic.claude-opus-5-5) | ce-skill-shipped | 90 | 28 | 1 | 61 | 38 | 6.62 / 7.36 | 7.24 / 7.94 | 0.79 / 0.46 | 10.40 / 10.42 |
| gpt (gpt-6.1-sol) | ce-skill-shipped | 90 | 36 | 5 | 49 | 43 | 6.17 / 6.57 | 6.73 / 7.03 | 2.40 / 1.73 | 8.07 / 8.36 |

ce-skill-shipped: the two judges pick the same winner in 65/90 pairs.
| opus (us.anthropic.claude-opus-5-5) | ce-skill-stale | 90 | 25 | 2 | 63 | 36 | 6.56 / 7.42 | 7.28 / 8.02 | 0.74 / 0.40 | 10.57 / 10.41 |
| gpt (gpt-6.1-sol) | ce-skill-stale | 90 | 25 | 11 | 54 | 38 | 5.88 / 6.64 | 6.57 / 7.17 | 2.16 / 1.54 | 8.32 / 7.56 |

ce-skill-stale: the two judges pick the same winner in 60/90 pairs.
| opus (us.anthropic.claude-opus-5-5) | ce-skill-slim | 90 | 23 | 0 | 67 | 42 | 6.71 / 7.53 | 7.61 / 8.00 | 0.53 / 0.38 | 10.53 / 10.93 |
| gpt (gpt-6.1-sol) | ce-skill-slim | 90 | 38 | 8 | 44 | 42 | 6.32 / 6.41 | 6.89 / 6.81 | 1.54 / 1.76 | 7.78 / 7.73 |

ce-skill-slim: the two judges pick the same winner in 47/90 pairs.

Paired deltas, treatment − normal (n = prompts; repeats averaged within a prompt; Holm across every row of this table):

| metric | n | mean Δ | 95% CI | sign-test p | Holm p |
|---|---|---|---|---|---|
| ce − normal: turns | 30 | -0.644 | [-2.88, +1.82] | 1.000 | 1.000 |
| ce − normal: total tokens | 30 | +44,706 | [-7,893, +108,309] | 0.001 | 0.104 |
| ce − normal: output tokens | 30 | -185 | [-806, +359] | 0.856 | 1.000 |
| ce − normal: $ / run (Bedrock list) | 30 | +0.000595 | [-0.0192, +0.0185] | 0.585 | 1.000 |
| ce − normal: wall s | 30 | +4.35 | [+0.234, +8] | 0.043 | 1.000 |
| ce − normal: tool calls | 30 | -0.644 | [-2.88, +1.82] | 1.000 | 1.000 |
| ce − normal: hallucinated-citation rate | 30 | -4.91e-05 | [-0.00113, +0.00098] | 1.000 | 1.000 |
| ce − normal: citations (descriptive) | 30 | +0.289 | [-4.09, +5.14] | 0.585 | 1.000 |
| ce − normal: opus: preference (+1 treatment win, −1 normal win) | 30 | -0.0333 | [-0.233, +0.167] | 1.000 | 1.000 |
| ce − normal: opus: overall score (1–10) | 30 | -0.0778 | [-0.522, +0.367] | 0.839 | 1.000 |
| ce − normal: opus: correctness score (1–10) | 30 | -0.267 | [-0.622, +0.0889] | 0.210 | 1.000 |
| ce − normal: opus: specificity score (1–10) | 30 | +0.0444 | [-0.244, +0.333] | 0.541 | 1.000 |
| ce − normal: opus: false claims per answer | 30 | +0.133 | [-0.144, +0.411] | 0.167 | 1.000 |
| ce − normal: opus: verified findings per answer (descriptive) | 30 | +0.878 | [-0.4, +2.4] | 0.845 | 1.000 |
| ce − normal: gpt: preference (+1 treatment win, −1 normal win) | 30 | +0.0222 | [-0.189, +0.233] | 1.000 | 1.000 |
| ce − normal: gpt: overall score (1–10) | 30 | -0.144 | [-0.722, +0.433] | 1.000 | 1.000 |
| ce − normal: gpt: correctness score (1–10) | 30 | -0.267 | [-0.967, +0.411] | 1.000 | 1.000 |
| ce − normal: gpt: specificity score (1–10) | 30 | +0.0778 | [-0.122, +0.289] | 0.839 | 1.000 |
| ce − normal: gpt: false claims per answer | 30 | +0.289 | [-0.444, +1.12] | 1.000 | 1.000 |
| ce − normal: gpt: verified findings per answer (descriptive) | 30 | +1.26 | [+0.111, +2.7] | 0.442 | 1.000 |
| ce-skill-shipped − normal: turns | 30 | -2.78 | [-5.21, -0.678] | 0.200 | 1.000 |
| ce-skill-shipped − normal: total tokens | 30 | +106,648 | [-6,413, +184,215] | 0.000 | 0.001 |
| ce-skill-shipped − normal: output tokens | 30 | -1,296 | [-2,101, -613] | 0.043 | 1.000 |
| ce-skill-shipped − normal: $ / run (Bedrock list) | 30 | +0.0434 | [+0.0101, +0.0692] | 0.000 | 0.005 |
| ce-skill-shipped − normal: wall s | 30 | +10.6 | [-0.647, +20.8] | 0.005 | 0.350 |
| ce-skill-shipped − normal: tool calls | 30 | -3.78 | [-6.21, -1.68] | 0.200 | 1.000 |
| ce-skill-shipped − normal: hallucinated-citation rate | 30 | -0.000786 | [-0.00351, +0.00115] | 1.000 | 1.000 |
| ce-skill-shipped − normal: citations (descriptive) | 30 | -0.267 | [-5.02, +4.64] | 0.200 | 1.000 |
| ce-skill-shipped − normal: opus: preference (+1 treatment win, −1 normal win) | 30 | -0.367 | [-0.6, -0.122] | 0.099 | 1.000 |
| ce-skill-shipped − normal: opus: overall score (1–10) | 30 | -0.733 | [-1.2, -0.267] | 0.052 | 1.000 |
| ce-skill-shipped − normal: opus: correctness score (1–10) | 30 | -0.7 | [-1.07, -0.356] | 0.004 | 0.257 |
| ce-skill-shipped − normal: opus: specificity score (1–10) | 30 | -0.656 | [-1, -0.322] | 0.019 | 1.000 |
| ce-skill-shipped − normal: opus: false claims per answer | 30 | +0.333 | [+0.0444, +0.644] | 0.023 | 1.000 |
| ce-skill-shipped − normal: opus: verified findings per answer (descriptive) | 30 | -0.0222 | [-1, +1.02] | 0.327 | 1.000 |
| ce-skill-shipped − normal: gpt: preference (+1 treatment win, −1 normal win) | 30 | -0.144 | [-0.333, +0.0444] | 0.052 | 1.000 |
| ce-skill-shipped − normal: gpt: overall score (1–10) | 30 | -0.4 | [-0.856, +0.0556] | 0.076 | 1.000 |
| ce-skill-shipped − normal: gpt: correctness score (1–10) | 30 | -0.3 | [-0.767, +0.2] | 0.136 | 1.000 |
| ce-skill-shipped − normal: gpt: specificity score (1–10) | 30 | -0.3 | [-0.589, -0.0556] | 0.093 | 1.000 |
| ce-skill-shipped − normal: gpt: false claims per answer | 30 | +0.667 | [-0.178, +1.83] | 0.832 | 1.000 |
| ce-skill-shipped − normal: gpt: verified findings per answer (descriptive) | 30 | -0.289 | [-1.77, +0.978] | 0.541 | 1.000 |
| ce-skill-stale − normal: turns | 30 | -2.77 | [-4.79, -0.944] | 0.099 | 1.000 |
| ce-skill-stale − normal: total tokens | 30 | -663 | [-100,148, +66,241] | 0.005 | 0.350 |
| ce-skill-stale − normal: output tokens | 30 | -1,415 | [-2,122, -793] | 0.005 | 0.350 |
| ce-skill-stale − normal: $ / run (Bedrock list) | 30 | -0.00608 | [-0.0357, +0.0183] | 0.099 | 1.000 |
| ce-skill-stale − normal: wall s | 30 | +3.36 | [-4.92, +10.8] | 0.043 | 1.000 |
| ce-skill-stale − normal: tool calls | 30 | -3.77 | [-5.79, -1.94] | 0.043 | 1.000 |
| ce-skill-stale − normal: hallucinated-citation rate | 30 | -0.000117 | [-0.003, +0.00211] | 0.625 | 1.000 |
| ce-skill-stale − normal: citations (descriptive) | 30 | -0.222 | [-5.43, +6.3] | 0.362 | 1.000 |
| ce-skill-stale − normal: opus: preference (+1 treatment win, −1 normal win) | 30 | -0.422 | [-0.589, -0.244] | 0.002 | 0.167 |
| ce-skill-stale − normal: opus: overall score (1–10) | 30 | -0.867 | [-1.24, -0.489] | 0.001 | 0.040 |
| ce-skill-stale − normal: opus: correctness score (1–10) | 30 | -0.744 | [-1.06, -0.444] | 0.000 | 0.007 |
| ce-skill-stale − normal: opus: specificity score (1–10) | 30 | -0.644 | [-0.911, -0.378] | 0.002 | 0.167 |
| ce-skill-stale − normal: opus: false claims per answer | 30 | +0.344 | [+0.1, +0.6] | 0.052 | 1.000 |
| ce-skill-stale − normal: opus: verified findings per answer (descriptive) | 30 | +0.156 | [-0.889, +1.42] | 0.248 | 1.000 |
| ce-skill-stale − normal: gpt: preference (+1 treatment win, −1 normal win) | 30 | -0.322 | [-0.533, -0.1] | 0.019 | 1.000 |
| ce-skill-stale − normal: gpt: overall score (1–10) | 30 | -0.767 | [-1.26, -0.3] | 0.019 | 1.000 |
| ce-skill-stale − normal: gpt: correctness score (1–10) | 30 | -0.6 | [-1.13, -0.0667] | 0.108 | 1.000 |
| ce-skill-stale − normal: gpt: specificity score (1–10) | 30 | -0.389 | [-0.6, -0.2] | 0.003 | 0.180 |
| ce-skill-stale − normal: gpt: false claims per answer | 30 | +0.611 | [+0.144, +1.1] | 0.169 | 1.000 |
| ce-skill-stale − normal: gpt: verified findings per answer (descriptive) | 30 | +0.767 | [-0.378, +2.2] | 0.108 | 1.000 |
| ce-skill-slim − normal: turns | 30 | -2.88 | [-4.76, -1.14] | 0.099 | 1.000 |
| ce-skill-slim − normal: total tokens | 30 | -20,037 | [-101,232, +41,108] | 0.016 | 1.000 |
| ce-skill-slim − normal: output tokens | 30 | -1,394 | [-2,031, -840] | 0.000 | 0.005 |
| ce-skill-slim − normal: $ / run (Bedrock list) | 30 | -0.0243 | [-0.0498, -0.00283] | 0.585 | 1.000 |
| ce-skill-slim − normal: wall s | 30 | -5.02 | [-11.3, +0.237] | 0.856 | 1.000 |
| ce-skill-slim − normal: tool calls | 30 | -3.88 | [-5.76, -2.14] | 0.099 | 1.000 |
| ce-skill-slim − normal: hallucinated-citation rate | 30 | -0.000725 | [-0.00351, +0.00133] | 1.000 | 1.000 |
| ce-skill-slim − normal: citations (descriptive) | 30 | -0.822 | [-5.44, +4.9] | 0.200 | 1.000 |
| ce-skill-slim − normal: opus: preference (+1 treatment win, −1 normal win) | 30 | -0.489 | [-0.644, -0.333] | 0.000 | 0.005 |
| ce-skill-slim − normal: opus: overall score (1–10) | 30 | -0.822 | [-1.16, -0.511] | 0.001 | 0.068 |
| ce-skill-slim − normal: opus: correctness score (1–10) | 30 | -0.389 | [-0.656, -0.122] | 0.007 | 0.423 |
| ce-skill-slim − normal: opus: specificity score (1–10) | 30 | -0.4 | [-0.622, -0.178] | 0.009 | 0.589 |
| ce-skill-slim − normal: opus: false claims per answer | 30 | +0.156 | [-0.0333, +0.356] | 0.332 | 1.000 |
| ce-skill-slim − normal: opus: verified findings per answer (descriptive) | 30 | -0.4 | [-1.44, +0.922] | 0.004 | 0.257 |
| ce-skill-slim − normal: gpt: preference (+1 treatment win, −1 normal win) | 30 | -0.0667 | [-0.244, +0.111] | 0.856 | 1.000 |
| ce-skill-slim − normal: gpt: overall score (1–10) | 30 | -0.0889 | [-0.611, +0.389] | 0.851 | 1.000 |
| ce-skill-slim − normal: gpt: correctness score (1–10) | 30 | +0.0778 | [-0.522, +0.656] | 1.000 | 1.000 |
| ce-skill-slim − normal: gpt: specificity score (1–10) | 30 | -0.0333 | [-0.222, +0.144] | 0.832 | 1.000 |
| ce-skill-slim − normal: gpt: false claims per answer | 30 | -0.211 | [-0.689, +0.222] | 1.000 | 1.000 |
| ce-skill-slim − normal: gpt: verified findings per answer (descriptive) | 30 | +0.0444 | [-0.856, +1.34] | 0.093 | 1.000 |

## haiku55 (`us.anthropic.claude-haiku-5-5`)

Complete sets (every arm present): 90 over 30 prompts. Valid runs outside a complete set (excluded from descriptives, still used in any pair they complete): none. Sets whose runs differ in Claude Code version or model: #1 r2.

| arm | runs | turns mean (median) | total tokens mean | input / cache-write / cache-read / output (mean) | $/run | $ total | wall s mean | tool calls/run | Read calls/run | CE calls/run | runs calling CE | Skill calls/run | runs at a ceiling | extra attempts | citations/run | cited regions found | hallucinated citations (pooled) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| normal | 90 | 26.7 (24) | 690,513 | 33 / 49,783 / 623,502 / 17,196 | 0.0266 | 2.39 | 107 | 25.7 | 8.64 | 0.00 | 0/90 | 0.00 | 0 | 0 | 34.9 | 0.989 | 7/3116 = 0.002 |
| ce | 90 | 26.6 (24) | 639,746 | 27 / 47,730 / 576,658 / 15,331 | 0.0248 | 2.23 | 93 | 25.6 | 11.20 | 0.83 | 53/90 | 0.00 | 0 | 0 | 35.4 | 0.990 | 5/3158 = 0.002 |
| ce-skill-stale | 90 | 24.3 (22) | 557,522 | 24 / 48,246 / 498,919 / 10,333 | 0.0199 | 1.79 | 84 | 22.3 | 10.48 | 2.00 | 77/90 | 0.98 | 0 | 0 | 28.3 | 0.995 | 1/2535 = 0.000 |

Tool calls per run by tool (mean over complete sets):

| arm | Bash | Glob | Grep | Read | Skill | ce:repo_search | ce:search | ce:symbol_graph |
|---|---|---|---|---|---|---|---|---|
| normal | 10.06 | 0.16 | 6.84 | 8.64 | 0.00 | 0.00 | 0.00 | 0.00 |
| ce | 5.82 | 0.09 | 7.68 | 11.20 | 0.00 | 0.68 | 0.01 | 0.14 |
| ce-skill-stale | 1.73 | 0.04 | 7.06 | 10.48 | 0.98 | 1.60 | 0.24 | 0.16 |

Blind pairwise judges, each treatment arm against normal on its own pairs (order balanced: Opus sees the treatment as report X in half the pairs per repeat, GPT sees every pair in the opposite order):

| judge | treatment | pairs | treatment wins | ties | normal wins | report-X wins (position check) | overall t / n | correctness t / n | false claims/answer t / n | verified/answer t / n |
|---|---|---|---|---|---|---|---|---|---|---|
| opus (us.anthropic.claude-opus-5-5) | ce | 90 | 41 | 0 | 49 | 42 | 7.28 / 7.27 | 7.90 / 7.81 | 0.37 / 0.48 | 8.72 / 8.62 |
| gpt (gpt-6.1-sol) | ce | 90 | 38 | 5 | 47 | 37 | 6.79 / 6.94 | 7.14 / 7.39 | 1.24 / 1.01 | 7.07 / 6.74 |

ce: the two judges pick the same winner in 60/90 pairs.

Paired deltas, treatment − normal (n = prompts; repeats averaged within a prompt; Holm across every row of this table):

| metric | n | mean Δ | 95% CI | sign-test p | Holm p |
|---|---|---|---|---|---|
| ce − normal: turns | 30 | -0.0889 | [-2.78, +2.62] | 0.856 | 1.000 |
| ce − normal: total tokens | 30 | -50,767 | [-129,994, +24,551] | 1.000 | 1.000 |
| ce − normal: output tokens | 30 | -1,865 | [-3,138, -656] | 0.016 | 0.419 |
| ce − normal: $ / run (Bedrock list) | 30 | -0.0018 | [-0.00788, +0.00398] | 0.585 | 1.000 |
| ce − normal: wall s | 30 | -13.7 | [-38.2, +2.24] | 0.856 | 1.000 |
| ce − normal: tool calls | 30 | -0.0778 | [-2.78, +2.63] | 0.856 | 1.000 |
| ce − normal: hallucinated-citation rate | 30 | -0.000771 | [-0.00387, +0.00215] | 1.000 | 1.000 |
| ce − normal: citations (descriptive) | 30 | +0.478 | [-2.74, +3.61] | 1.000 | 1.000 |
| ce − normal: opus: preference (+1 treatment win, −1 normal win) | 30 | -0.0889 | [-0.289, +0.0889] | 1.000 | 1.000 |
| ce − normal: opus: overall score (1–10) | 30 | +0.0111 | [-0.344, +0.367] | 1.000 | 1.000 |
| ce − normal: opus: correctness score (1–10) | 30 | +0.0889 | [-0.211, +0.389] | 1.000 | 1.000 |
| ce − normal: opus: specificity score (1–10) | 30 | -0.144 | [-0.433, +0.122] | 0.541 | 1.000 |
| ce − normal: opus: false claims per answer | 30 | -0.111 | [-0.356, +0.122] | 1.000 | 1.000 |
| ce − normal: opus: verified findings per answer (descriptive) | 30 | +0.1 | [-0.578, +0.822] | 0.701 | 1.000 |
| ce − normal: gpt: preference (+1 treatment win, −1 normal win) | 30 | -0.1 | [-0.267, +0.0667] | 0.458 | 1.000 |
| ce − normal: gpt: overall score (1–10) | 30 | -0.156 | [-0.522, +0.2] | 0.557 | 1.000 |
| ce − normal: gpt: correctness score (1–10) | 30 | -0.244 | [-0.711, +0.211] | 0.839 | 1.000 |
| ce − normal: gpt: specificity score (1–10) | 30 | -0.167 | [-0.344, -3.7e-18] | 0.286 | 1.000 |
| ce − normal: gpt: false claims per answer | 30 | +0.233 | [-0.0889, +0.578] | 0.307 | 1.000 |
| ce − normal: gpt: verified findings per answer (descriptive) | 30 | +0.322 | [-0.456, +1.16] | 1.000 | 1.000 |
| ce-skill-stale − normal: turns | 30 | -2.44 | [-5.71, +0.533] | 0.458 | 1.000 |
| ce-skill-stale − normal: total tokens | 30 | -132,991 | [-290,363, +6,988] | 0.200 | 1.000 |
| ce-skill-stale − normal: output tokens | 30 | -6,863 | [-8,839, -5,013] | 0.000 | 0.000 |
| ce-skill-stale − normal: $ / run (Bedrock list) | 30 | -0.00675 | [-0.0136, -0.00183] | 0.362 | 1.000 |
| ce-skill-stale − normal: wall s | 30 | -23.5 | [-35.9, -11.7] | 0.043 | 1.000 |
| ce-skill-stale − normal: tool calls | 30 | -3.41 | [-6.68, -0.433] | 0.200 | 1.000 |
| ce-skill-stale − normal: hallucinated-citation rate | 30 | -0.00232 | [-0.00488, -0.000286] | 0.062 | 1.000 |
| ce-skill-stale − normal: citations (descriptive) | 30 | -6.61 | [-9.69, -3.71] | 0.013 | 0.339 |

## Findings in brief

- **Plain `ce` (CE MCP, no instructions): no detectable effect on either model.**
  - Sonnet 5.5: cost +$0.0006/run, judge preference −0.03 (Opus) and +0.02 (GPT), correctness −0.27 for both judges. Every CI spans 0.
  - Haiku 5.5: cost −$0.002/run, preference −0.09 (Opus) and −0.10 (GPT), output tokens −1.9k (Holm p 0.42).
  - Without being told to, the agents rarely use CE: 27/90 Sonnet runs and 53/90 Haiku runs call a CE tool, 0.3 and 0.8 calls/run.
  - CE's tool definitions add about +45k cache-read tokens/run on Sonnet, but that costs almost nothing at cache-read prices.
- **`ce-skill-shipped` (the skill the extension ships, Sonnet only): adoption goes to 100%, cost goes up, quality goes down.**
  - Adoption: every run invokes the skill, and 73/90 call CE.
  - Cost: +$0.043/run (+30%, Holm p 0.005) and +107k tokens/run, because the 1,357-line skill is loaded into context.
  - Work done: 3.8 fewer tool calls and 1.3k fewer output tokens per run.
  - Opus prefers normal: 61 wins to 28; preference −0.37 [−0.60, −0.12]; correctness −0.70 [−1.07, −0.36] (sign p 0.004, Holm p 0.26); false claims +0.33/answer.
  - GPT points the same way but less clearly: 49 to 36, preference −0.14 [−0.33, +0.04].
- **`ce-skill-stale` (an old monorepo copy, sha 4375a218, not the shipped file): the worst arm on Sonnet.**
  - Opus: 63 losses to 25, overall −0.87 (Holm p 0.040), correctness −0.74 (Holm p 0.007).
  - That file says "NEVER use Read File or grep for exploration". Its runs did about 4 fewer tool calls and checked less.
  - On Haiku the stale arm was cheaper (−$0.007/run) and faster. By decision it was not judged on Haiku.
- **`ce-skill-slim` (a 27-line candidate skill, sha 637e438c, Sonnet only, CE build c9f17e8d): the cheapest arm; the two judges disagree on quality.**
  - Adoption: every run invokes the skill, and 65/90 call CE (0.74 calls/run).
  - Cost: −$0.024/run vs normal [−0.050, −0.003] (sign p 0.59), the cheapest arm. 1.4k fewer output tokens (Holm p 0.005), 3.9 fewer tool calls, 1.06 Reads/run against normal's 3.77.
  - Opus prefers normal: 67 wins to 23, preference −0.49 [−0.64, −0.33] (Holm p 0.005); overall −0.82 (Holm p 0.068); correctness −0.39 [−0.66, −0.12] (Holm p 0.42); specificity −0.40.
  - GPT sees no difference: 44 to 38, preference −0.07 [−0.24, +0.11]; correctness +0.08; false claims −0.21/answer.
  - The judges agree on the winner in only 47/90 slim pairs, against 60–65 for the other arms.
  - False claims: Opus counts +0.16/answer for slim, under the shipped skill's +0.33.
- **Hallucinated citations are rare in every arm, 0.0–0.2%.** No arm differs (all CIs span 0).
- **Neither judge is told a tool arm exists.** They agree on the winner in 60–65 of 90 pairs.

## Where cited line numbers came from (sonnet55; pooled over valid citations, first matching source)

| arm | runs | citations | numbered | ce_number | ce_snippet | unnumbered | unseen |
|---|---|---|---|---|---|---|---|
| normal | 90 | 3154 | 79.9% (2520) | 0.0% (0) | 0.0% (0) | 6.4% (202) | 13.7% (432) |
| ce | 90 | 3153 | 78.4% (2472) | 0.4% (12) | 0.6% (19) | 9.9% (311) | 10.8% (339) |
| ce-skill-shipped | 90 | 3123 | 81.1% (2534) | 1.4% (44) | 0.8% (25) | 6.3% (197) | 10.3% (323) |
| ce-skill-slim | 90 | 3080 | 78.5% (2417) | 1.6% (48) | 0.9% (29) | 7.6% (234) | 11.4% (352) |
| ce-skill-stale | 90 | 3139 | 79.9% (2507) | 1.2% (37) | 0.7% (21) | 5.8% (183) | 12.5% (391) |

How the classes are drawn is in `audit/cite_sources.py`. Line numbers counted inside CE snippets are under 1% of
citations in every arm (slim 0.9%, shipped 0.8%), so that does not explain the skill arms' losses. In every arm,
78–81% of cited lines were shown with their numbers by Read or `grep -n`. 10–14% of cited lines, normal included,
never appeared in any tool output: the model cites Gitea from memory, and such lines usually exist but were never
checked.

## Things that make the comparison less than perfectly fair

1. **The prompts have no gold answers.**
   - GrapeRoot's prompts are open-ended audits ("find places where …"). We score precision-like quality: findings verified against the cited code, specificity and false claims. We do not score recall.
   - The judges see code only at the cited locations. A real finding whose proof sits elsewhere (e.g. a caller) can only be graded "plausible". That limit is the same for every arm.
2. **The CE index was incomplete until just before the CE runs.**
   - CE's default excludes had skipped gitea's root `models/` directory (380 Go files, the data layer), and about 600 uploaded files were never signalled for indexing.
   - Both were fixed server-side, and a fresh full index finished at 10:09Z on 2026-10-21. Then the gate passed: 40/40 sampled files indexed, 5/5 dev gold fresh, routed.
   - Every CE run used build `6f0ceb92…` and that complete index. Earlier builds served only the smoke normal arm, which never calls CE.
3. **The skill arms came in mid-study.**
   - The coordinator added `ce-skill` after the smoke run. It first ran with an outdated SKILL.md from the retired monorepo (`ce-skill-stale`).
   - The shipped file (`ce-skill-shipped`, Context-Engine-Extension 187f674 = release 2.3.27) then ran on Sonnet only, 12:40–13:01Z, about 1.5 h after the other Sonnet arms (10:35–11:18Z).
   - Its pairing with the normal runs (same prompt, same repeat, same build, same Claude Code 2.1.304) is valid for tokens, cost and quality. Its wall time is confounded with time of day.
   - `ce-skill-slim` ran later still (2026-10-21, after 14:00Z) on a newer frozen CE build, `c9f17e8d…` (source 07c3f4e1). That build adds `symbol_start_line` to search results. The gate was re-checked first: coverage 1.00, 5/5 gold fresh.
     - So the slim arm differs from the shipped arm in both skill and CE build. Its pairing with normal (which never calls CE) is still clean.
     - Its SKILL.md lives in `work/skills/slim/`, outside git, so the receipt pins it by sha256 (`637e438c…`).
     - The skill mentions CE tools this harness does not expose (`qdrant_list`, `set_session_defaults`, `qdrant_status`, `cross_repo_search`), as in every CE arm.
   - Adding arms grows the Holm family, so Holm p values for the earlier Sonnet arms rose slightly from earlier versions of this file.
   - The skill arms load user-level settings from a fresh, otherwise empty config dir (`--setting-sources user`) where the other arms load none. That is the only way to give Claude Code the skill the way the extension installs it.
4. **Claude Code updated itself mid-run.**
   - The installed `claude` auto-updated from 2.1.304 to 2.1.306 at about 11:40Z, during the Haiku batch.
   - Haiku repeat 1 ran on 2.1.304; repeats 2–3 ran on 2.1.306 (one set, #1 r2, straddles the switch). The arms were interleaved, so each Haiku pair shares a version except #1 r2.
   - After that, runs called the 2.1.304 binary directly. All Sonnet runs, every arm included, are on 2.1.304. 7 shipped-arm runs that had started on 2.1.306 were discarded and rerun (counted in "extra attempts" and in spend).
   - The Opus judge for Haiku ran on 2.1.306; it uses our own system prompt and no tools.
5. **The judges have quirks.**
   - Order is balanced: each pair is judged once in each order across the two judges.
   - GPT-6.1 is stricter. It marks unconfirmable claims (e.g. "this function is dead") as false, and gives more false claims per answer in every arm.
   - Opus 5.5 belongs to the same model family as the agents. Both arms are Claude-written, so this should not favour either arm. GPT is the cross-family check.
6. **The smoke run's arms ran 10.5 h apart**, as instructed. The full set interleaves its arms, except the late shipped-skill arm.
7. **Scrubbing hides tool names, not method talk.** Tool and CE mentions are scrubbed before judging. Generic method talk ("I searched routers/") survives in all arms.
8. **Agents could write outside the tree.**
   - The deny list blocks network, git, edits and interpreters, but shell redirection to /tmp is possible. One Haiku agent ran a background scan into /tmp.
   - The pinned tree's manifest was unchanged after every batch.
   - That background job made Claude Code emit a second result segment. The harness joins segments into one answer and adds up their turns and usage.
9. **Dollars are reconstructed, and only for Claude.**
   - They are Anthropic list x 1.1, for US cross-region Bedrock profiles; the Bedrock pricing page did not render its Claude rows.
   - GPT-6.1 judge usage (11.1M input / 2.0M output tokens over all judgments) is billed to a separate Azure deployment and not included.
10. `turns` and `tool calls` move together (Claude Code counts a turn per tool round trip). The few `Write` calls are attempts at a tool the arm does not offer; they were refused.

## Spend

The total is **$145.71** at the rates above.

| Item | Spend |
|---|---|
| Full-set agent runs | $62.36 |
| Full-set Opus judgments | $52.45 |
| `ce-skill-slim` agent runs | $10.95 |
| `ce-skill-slim` Opus judgments | $12.06 |
| Smoke runs | $3.04 |
| Smoke Opus judgments | $1.29 |
| Runs set aside and rerun | $1.64 |
| Runs killed mid-flight (estimated) | $1.93 |

Not included: about $0.7 of unrecorded probes and partial runs.


