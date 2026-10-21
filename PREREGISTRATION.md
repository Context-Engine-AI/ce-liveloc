# CE-LiveLoc pre-registration

Frozen 2026-10-20, before any test-split run. Everything below is fixed for the test split. The git commit that
adds this file is the timestamp.

## Hypothesis

Giving a coding agent Context Engine (CE) retrieval reduces the tokens it spends before it first sees the code
that must change (tokens-to-gold, TtG). We test four ways of delivering CE (B, F, H, P) against built-in tools (A).

## Task set (frozen)

- `data/tasks.jsonl`: 149 tasks from 6 third-party repos, about 25 each. sha256 `7653e74d311210b24686702240d8fa0922852381023bd07205f9ef65816a7e4c`.
- **Test split** (`data/split-test.txt`): 119 tasks. sha256 of its task lines (`json.dumps(t, sort_keys=True)`,
  one per line, in file order) is `ec1e294b1c5f25170dd5a88a3fe0331a9f12256740c471325e99981a1de9f204`.
- **Dev split** (`data/split-dev.txt`): 30 tasks, 5 per repo. sha256 `d74ae60311f5bc11cc59492233ef4a7ebed0f212bf01583e8ec457b51f03d839`.
  Dev runs may tune the harness. Test runs may not.
- Each repo is indexed once, at one snapshot: its last default-branch commit before 2026-09-01T00:00Z (`data/repos.json`).
- Every task comes from a PR merged into the default branch between 2026-09-01 and 2026-10-19. That is after the
  snapshot and after 2026-07-01.
- The PR must close a same-repo issue whose description is at least 120 characters, after HTML comments are
  stripped. The prompt is that issue's title and body.
- **Gold** is the 1–4 non-test source files the PR modified.
- **Validity checks.** Every gold blob is identical at the snapshot, at the PR's `baseRefOid` and at the merge
  commit's first parent. Every hunk's old-side lines equal the snapshot file exactly. Every gold file is uploaded
  by the CE bridge.
- Selection and the split are sha256 orders under the seed `ce-liveloc-20261020` (`harness/build_tasks.py`).

## Arms

All arms get the same prompt, the issue plus a fixed localization instruction (`harness/run.py:PROMPT`), and the
same limits: `--max-turns 15`, `--max-budget-usd 1`, 600 s.

| Arm | Tools |
|---|---|
| A | Read, Grep, Glob, Bash. Network, git, edit and interpreter commands are denied. |
| B | A, plus CE MCP (`search`, `repo_search`, `symbol_graph`, `context_answer`, `graph_query`) through the CE proxy. The collection is routed by `X-Collection` from the cwd's workspace mapping; the prompt does not mention CE. |
| F | B, plus one appended system instruction: "Use the Context Engine tools (repo_search / search / symbol_graph) to locate code before Grep/Glob/Read; use Grep only for exact-string checks." |
| H | A, plus a PreToolUse hook on Grep/Glob/Bash(grep\|rg). The hook sends the pattern to CE `search` (3 s timeout) and injects the top 5 hits (path:lines, symbol, first line; at most 1.5 KB) as `additionalContext`. |
| P | B's tools with A's prompt, plus the CE Claude Code plugin ([Context-Engine-AI/claude-plugins](https://github.com/Context-Engine-AI/claude-plugins) @ `5c865ce`, loaded with `--plugin-dir`). It adds a SessionStart/SubagentStart note and denies at most the first 2 concept or identifier greps per session, naming the exact CE call. (Amendment, 2026-10-20 16:10Z, before any test run. Verified: with `--setting-sources ""` the plugin's SessionStart note and its PreToolUse deny both fire.) |

Models: Haiku 5.5 (`us.anthropic.claude-haiku-5-5`) and Sonnet 5.5 (`us.anthropic.claude-sonnet-5-5`), one seed
each. The model is analysed as a separate family.

## Endpoints

The unit is the task, paired across arms.

**Primary:** TtG, compared as log2 per task. A run that never sees gold is censored at 200,000 tokens. TtG is
measured as context growth: prompt tokens of the API call after the reveal, minus prompt tokens of the first call.
"Gold seen" means any of:

- a Read of a gold file;
- Grep content lines, or Bash grep/cat/sed output, from a gold file;
- a CE result or hook row whose returned code matches the snapshot file at the claimed lines.

Paths alone never count.

**Secondary:**

- found@8k, found@32k and found@100k;
- turns-to-gold, censored at 16;
- final-answer file recall and precision;
- function/hunk hit (a cited span overlaps a gold function or changed lines ±3);
- cost, total tokens and wall time.

We also report adoption: CE calls per run, the share of runs that use CE, and hook hits per run.

**Strata**, reported but not tested:

- language;
- repo;
- `leak_path`, i.e. whether the issue names a gold path or filename;
- number of gold files.

## Analysis

- For each model, each arm B/F/H/P is compared with A on the per-task difference.
- The 95% CI is a bootstrap over tasks (10,000 draws, seed 7).
- p is a sign-flip permutation test (exact for n ≤ 16, else 20,000 draws), with Holm correction across the four
  arms. The primary claim is at α = 0.05.
- A run is invalid if the CLI produced no result event or the run timed out. An invalid run is retried once; a
  task still invalid in any compared arm drops out of that pair.
- Every run records the CE `mcp-indexer-http` image digest. A batch whose digest changes mid-run is re-run.
- Each collection must pass the freshness and routing gate (`harness/freshness.py`) before any run.
- No test-split result is excluded after the fact. The test split is run once per model.

## Amendment: GrapeRoot-Gitea audit, arm `ce-skill-locate` (2026-10-21T18:11Z, before any locate run)

This amendment covers `audit/`, a separate A/B on GrapeRoot's Gitea audit prompts. It is not part of the LiveLoc
analysis above.

- **Arm.** `ce-skill-locate` = `ce-skill-slim`'s setup with `audit/skills/locate/SKILL.md` (sha256
  `d64429c1140558294dfe33cb4761f74cbd9e6d628787148fb89aee79a90f6ff3`).
  - Sonnet 5.5, 30 prompts × 3 repeats, Claude Code 2.1.304, CE build `8f9c8785…`.
  - Paired with the existing Sonnet normal runs by prompt and repeat. Judged by Opus 5.5 and GPT-6.1 with unchanged
    settings.
- **Primary readout: non-inferiority to `normal` on two Opus-judged metrics.** Each is the mean per-prompt paired
  difference (locate − normal, repeats averaged):
  - correctness score (1–10): margin **−0.5 points**;
  - preference (+1 locate win / 0 tie / −1 normal win per pair): margin **−0.2**, i.e. normal winning 10 percentage
    points more pairs than locate.
- **Rule.** Locate is non-inferior on a metric if the lower bound of its 95% bootstrap CI (prompt-clustered, 10,000
  draws, seed 7) is above the margin.
  - GPT-6.1 results on the same metrics are secondary.
  - Holm-adjusted p values over the whole table are reported as before, but the non-inferiority call uses the CI rule
    only.
- **Stop rule.** Stop the arm if any CE call in the gate or the first runs takes over 10 s.
- **Stop rule, updated 18:19Z before any locate run.** Replaces the 10 s rule above, at the
  coordinator's request after the 6–10 s Helion hybrid-stage tail was diagnosed as complete-but-late. Stop if any CE
  call takes over 30 s, or if any CE reply is degraded (`degraded: true`, a `*_status` of timeout or dropped, or a
  dense channel reported off). Margins and readout are unchanged.

## Amendment: pooled locate-vs-normal test on two repos (2026-10-21T20:09Z, before any run in this phase)

**Question:** is `ce-skill-locate` non-inferior to `normal` once CE search is fast? This phase answers it on more
prompts. The skill is unchanged: `audit/skills/locate/SKILL.md`, sha256 `d64429c1…`.

**Backend, frozen for the whole phase.**
- CE `mcp-indexer-http`: `bb48116d@sha256:8f9c8785…`.
- Helion, all helix-lsm pods: `1e40402c@sha256:d93d1f9d…`.
- Every receipt records both. A CE-arm run whose CE or Helion digest differs from the pin at its start or end is
  invalid and is rerun.
- The new arm is labelled `ce-skill-locate-fast`. The earlier gitea `ce-skill-locate` rows (same CE image, Helion
  before 1e40402c) keep their label and are reported separately. They are not part of the pooled test.

**Repos and prompts.** Prompts are read unmodified from the GrapeRoot checkout at cae330f3.

| repo | snapshot | prompts | normal arm | ce-skill-locate-fast arm |
|---|---|---|---|---|
| go-gitea/gitea | 4875bb3b | `gitea_go_30` (30) | the existing 90 Sonnet runs, reused | 90 new runs |
| calcom/cal.diy | 176037d0 | `typescript_monorepo_30` (30) | 90 new runs | 90 new runs, interleaved with normal |

- On cal.diy, the folder and collection are named `liveloc-calcom__cal-diy-176037d0`.
- Sonnet 5.5, 3 repeats, Claude Code 2.1.304, with the same flags, tools and isolated skill setup as before.
- The cal.diy collection must pass `harness/freshness.py` at coverage ≥ 0.95, routed, before any CE run.

**Judging.**
- Both judges run at unchanged settings: Opus 5.5 at effort high, and GPT-6.1 with shell and web off.
- The rubric changes only in naming the repo and language: "senior TypeScript engineer … (cal.com (calcom/cal.diy))"
  for cal.diy. Gitea's rubric text is unchanged.
- Report order stays balanced per (model, repeat, arm).

**Primary analysis, pooled.**
- Unit: one prompt, n = 60 (30 per repo).
- Per unit, take the locate-fast − normal paired difference averaged over its 3 repeats, then the mean over the
  60 units.
- CI: 95% bootstrap, resampling prompts (10,000 draws, seed 7).
- Two primary metrics, both Opus-judged, with margins unchanged from the earlier amendment:
  - correctness score: margin **−0.5**;
  - preference (+1 locate win / 0 tie / −1 normal win): margin **−0.2**.
- Locate is non-inferior on a metric if its CI lower bound is above the margin. Both are reported. "Locate is
  non-inferior" is claimed only if both pass.
- Secondary, reported in the same table: GPT on the same metrics; Opus and GPT overall, specificity and false
  claims; per-run cost, tokens, turns, Read calls and hallucinated-citation rate.
- Holm-adjusted p values (exact sign test) are given across every row of the pooled table. Per-repo rows are reported
  beside it as descriptive.

**Stop rules.**
- Stop if any CE call takes over 30 s, or if any CE reply is degraded (`degraded: true`, a `*_status` of timeout or
  dropped, or a dense channel reported off).
- A smoke on cal.diy runs first: 10 prompts x {normal, locate-fast} x repeat 1. These runs count toward the full set
  because the config is identical.
- CE latency per arm is reported: median, p90, max, and calls over 5 s.
