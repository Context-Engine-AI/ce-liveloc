# CE vs no-CE on GrapeRoot's Gitea audit prompts (smoke: Sonnet 5.5, 10 prompts, 1 run each)

- Prompts: https://github.com/kunal12203/graperoot-benchmarks @ `cae330f3db8588acb7ae8a22bb3f019a1b4a153a`, `prompts/gitea_go_30.json` (read at run time, not copied into this repo); prompt ids [1, 4, 7, 10, 13, 16, 19, 22, 25, 28].
- Code: go-gitea/gitea snapshot `4875bb3b2b4d9f2b1591b7b3b09d7517180fa7d4` (tree `liveloc-go-gitea__gitea-4875bb3b`, manifest `ccc9ad59ed87df79`); CE collection `admin_liveloc-go-gitea__gitea-4875bb3b-92106c05`.
- CE build (mcp-indexer-http image digest, pinned per batch, over all ce runs): `sha256:6f0ceb92656c5030cc217b80fae15df6bf8dbdbf5a973718009890792d7b4941`.
- Agent: Claude Code 2.1.304 headless. Judges: Claude Opus 5.5 (`us.anthropic.claude-opus-5-5`, Bedrock, `claude -p`, no tools) and GPT-6.1 (`gpt-6.1-sol`, `codex exec`, shell and web off).
- Dollars: Bedrock list = Anthropic list (platform.claude.com/docs/en/about-claude/pricing, fetched 2026-10-20) x 1.1: that page states Bedrock regional/multi-region endpoints carry a 10% premium over global for Claude 4.5+, and the us.anthropic.* IDs are US geo cross-region profiles. The Claude rows of aws.amazon.com/bedrock/pricing did not render, so the Bedrock page itself was not the source. Haiku 5.5 uses its >100k-prompt card per call. This report: agent runs $3.04, Opus judgments $1.29; all audit spend so far incl. set-aside runs $122.70. GPT-6.1 judge tokens are billed to a separate Azure deployment and not in these dollars (221,792 input / 32,289 output tokens).
- Arms: same prompt (GrapeRoot's text + one line asking for `path:line` citations); `--setting-sources ""` so no CLAUDE.md/AGENTS.md (the Gitea tree has both), user hooks or plugins; no stop, turn-budget or tool-use instructions; same built-in tools and deny list; identical silent ceilings (150 turns, $8, 45 min). `ce` adds only the CE MCP server, i.e. its tool definitions and the server's own initialize instructions (1426 chars, see `-ce-server.json`).
- Skill arms (added after the smoke run): `ce` + what the VS Code extension installs with `mcpClaudeSkillEnabled` (installSkillClaude): the skill folder copied verbatim plus the line "# Important, always use skill `context-engine`" prepended to CLAUDE.md, both in a fresh per-run `CLAUDE_CONFIG_DIR` loaded with `--setting-sources user`, plus the Skill tool; a run where Claude Code did not list the skill is invalid. `ce-skill-shipped`: SKILL.md sha256 `f64e9db1980d82fc…` from `Context-Engine-Extension@187f674`. `ce-skill-stale`: SKILL.md sha256 `4375a2180bee737c…` from `Context-Engine@24d0ff49`. **`ce-skill-shipped` is what the extension ships (Context-Engine-Extension 187f674, release 2.3.27). `ce-skill-stale` is an older copy from the retired monorepo, run by mistake first; it is reported as a finding about that file, not as the shipped product.** It has no opening frontmatter `---` and says "NEVER use Read File or grep for exploration". `ce-skill-shipped` ran on Sonnet only, after the other arms, so its wall time is not comparable; its tokens, cost and quality are paired with the same normal runs.
- Scoring: a hallucinated citation is a cited file missing from the pinned tree or a line past its end (`audit/cites.py`); the judges see both answers (tool mentions scrubbed) plus the pinned code at every cited location, pooled, and grade verified correctness, specificity and false claims with a rubric that gives volume no credit (`audit/judge.py`).

## sonnet55 (`us.anthropic.claude-sonnet-5-5`)

Complete sets (every arm present): 10 over 10 prompts. Valid runs outside a complete set (excluded from descriptives, still used in any pair they complete): none. Sets whose runs differ in Claude Code version or model: none.

| arm | runs | turns mean (median) | total tokens mean | input / cache-write / cache-read / output (mean) | $/run | $ total | wall s mean | tool calls/run | CE calls/run | runs calling CE | Skill calls/run | runs at a ceiling | extra attempts | citations/run | cited regions found | hallucinated citations (pooled) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| normal | 10 | 12.5 (8) | 225,538 | 17 / 24,285 / 196,409 / 4,827 | 0.1415 | 1.42 | 45 | 11.5 | 0.00 | 0/10 | 0.00 | 0 | 0 | 32.1 | 0.988 | 0/317 = 0.000 |
| ce | 10 | 12.2 (12) | 254,094 | 18 / 30,070 / 218,966 / 5,039 | 0.1623 | 1.62 | 61 | 11.2 | 0.30 | 3/10 | 0.00 | 0 | 0 | 29.2 | 1.000 | 0/292 = 0.000 |

Tool calls per run by tool (mean over complete sets):

| arm | Bash | Grep | Read | ce:repo_search |
|---|---|---|---|---|
| normal | 5.00 | 3.40 | 3.10 | 0.00 |
| ce | 7.00 | 2.80 | 1.10 | 0.30 |

Blind pairwise judges, each treatment arm against normal on its own pairs (order balanced: Opus sees the treatment as report X in half the pairs per repeat, GPT sees every pair in the opposite order):

| judge | treatment | pairs | treatment wins | ties | normal wins | report-X wins (position check) | overall t / n | correctness t / n | false claims/answer t / n | verified/answer t / n |
|---|---|---|---|---|---|---|---|---|---|---|
| opus (us.anthropic.claude-opus-5-5) | ce | 10 | 6 | 0 | 4 | 3 | 7.50 / 7.10 | 7.70 / 7.80 | 0.20 / 0.30 | 8.20 / 6.50 |
| gpt (gpt-6.1-sol) | ce | 10 | 5 | 0 | 5 | 8 | 6.40 / 6.60 | 6.60 / 7.10 | 3.70 / 1.20 | 7.60 / 5.20 |

ce: the two judges pick the same winner in 7/10 pairs.

Paired deltas, treatment − normal (n = prompts; repeats averaged within a prompt; Holm across every row of this table):

| metric | n | mean Δ | 95% CI | sign-test p | Holm p |
|---|---|---|---|---|---|
| ce − normal: turns | 10 | -0.3 | [-6.6, +3.9] | 0.180 | 1.000 |
| ce − normal: total tokens | 10 | +28,556 | [-102,846, +127,211] | 0.021 | 0.430 |
| ce − normal: output tokens | 10 | +212 | [-1,464, +1,700] | 0.344 | 1.000 |
| ce − normal: $ / run (Bedrock list) | 10 | +0.0207 | [-0.0275, +0.0594] | 0.109 | 1.000 |
| ce − normal: wall s | 10 | +16.1 | [+7.4, +25.7] | 0.021 | 0.430 |
| ce − normal: tool calls | 10 | -0.3 | [-6.6, +3.9] | 0.180 | 1.000 |
| ce − normal: hallucinated-citation rate | 10 | +0 | [+0, +0] | 1.000 | 1.000 |
| ce − normal: citations (descriptive) | 10 | -2.9 | [-10.3, +4] | 0.754 | 1.000 |
| ce − normal: opus: preference (+1 treatment win, −1 normal win) | 10 | +0.2 | [-0.4, +0.8] | 0.754 | 1.000 |
| ce − normal: opus: overall score (1–10) | 10 | +0.4 | [-0.5, +1.4] | 0.754 | 1.000 |
| ce − normal: opus: correctness score (1–10) | 10 | -0.1 | [-1.1, +0.9] | 1.000 | 1.000 |
| ce − normal: opus: specificity score (1–10) | 10 | -0.6 | [-1.4, +0.1] | 0.453 | 1.000 |
| ce − normal: opus: false claims per answer | 10 | -0.1 | [-0.4, +0.2] | 1.000 | 1.000 |
| ce − normal: opus: verified findings per answer (descriptive) | 10 | +1.7 | [+0.5, +3.2] | 0.109 | 1.000 |
| ce − normal: gpt: preference (+1 treatment win, −1 normal win) | 10 | +0 | [-0.6, +0.6] | 1.000 | 1.000 |
| ce − normal: gpt: overall score (1–10) | 10 | -0.2 | [-2, +1.4] | 1.000 | 1.000 |
| ce − normal: gpt: correctness score (1–10) | 10 | -0.5 | [-2.7, +1.5] | 1.000 | 1.000 |
| ce − normal: gpt: specificity score (1–10) | 10 | -0.4 | [-0.8, +0] | 0.250 | 1.000 |
| ce − normal: gpt: false claims per answer | 10 | +2.5 | [-0.8, +6.9] | 1.000 | 1.000 |
| ce − normal: gpt: verified findings per answer (descriptive) | 10 | +2.4 | [+0.5, +4.8] | 0.219 | 1.000 |

## Caveats for this smoke run

- **The two arms ran 10.5 h apart.** The user asked for the normal arm at once, without waiting for CE indexing. Normal ran
  2026-10-20 23:45Z and ce ran 2026-10-21 10:25Z. The +16 s wall-time difference may be Bedrock latency at different times
  of day, not CE. The normal runs carry the CE build that was serving then (`6b627254…`), but they never call CE. The
  full set interleaves the two arms per prompt on one pinned build.
- **CE was barely used.** 3 of the 10 ce runs called a CE tool, one `repo_search` each. The agent gets no instruction to
  use or avoid tools, and Sonnet 5.5 mostly used Grep and Bash. So the ce arm mainly pays for CE's tool definitions and
  server instructions in context (about +5.8k cache-write tokens per run) without using them.
- **The CE index was fixed before the ce arm ran.** CE's default excludes had skipped gitea's root `models/` directory
  (380 Go files, the whole data layer), and about 600 uploaded files were never signalled for indexing. Both were fixed
  server-side and gitea fully reindexed before the ce arm ran. The gate then showed 40/40 sampled files indexed,
  5/5 dev gold fresh, routed.
- **The judges have position bias and different strictness.** GPT-6.1 preferred report X in 8 of 10 pairs; Opus did in
  3 of 10. Because every pair is judged once in each order, the bias cancels across the two judges. GPT counts claims
  the cited code cannot confirm (e.g. "this exported function is dead") as false where Opus calls them plausible. That
  drives GPT's ce false-claim mean: 19 of its 37 come from one answer, #22. The judges pick the same winner in 7 of 10 pairs.
- **The sample is small.** n = 10 prompts, one run each, so no difference survives Holm correction. This run checks
  that the harness works; it does not support conclusions.
- `turns` and `tool calls` move together because Claude Code counts a turn per tool round trip.

