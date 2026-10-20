# CE-LiveLoc: live code localization, measured as tokens-to-gold

A benchmark built to be fast, sensitive and contamination-resistant. It asks how many tokens an agent burns
before it first sees the code that must change, and whether Context Engine (CE) shortens that.

- **Live.** Every task comes from a third-party PR merged between 2026-09-01 and 2026-10-19, after the models'
  training data.
- **Real issues.** The prompt is the closing issue's own title and body.
- **One index per repo.** Each repo is indexed once, at a snapshot taken before every PR. A task is kept only if
  every gold file is byte-identical between the snapshot and the PR's base, and every hunk is verified line by
  line against the snapshot. That is what lets a single index serve every task.
- **Large repos.** The repos have 3k–10k indexable files each, and one Grep rarely lands on the target.
- **Trace metrics.** Instead of a noisy pass/fail endpoint, each run is scored from its trace: tokens-to-gold,
  turns-to-gold, found@budget and final-answer precision/recall. Pairing by task across arms is what gives the
  sensitivity.
- **Cheap.** A localization run ends at the answer or at 15 turns. A Haiku 5.5 run costs about $0.01 and takes
  about 30 s.

## Repos (snapshot = last default-branch commit before 2026-09-01)

| Repo | Snapshot | Main language | Indexable files | Tasks (dev/test) |
|---|---|---|---|---|
| go-gitea/gitea | 4875bb3b | Go (+TS) | 3,763 | 24 (5/19) |
| zed-industries/zed | ce48461e | Rust | 3,094 | 25 (5/20) |
| godotengine/godot | 29e8dfdd | C++ (`thirdparty/` excluded) | 5,830 | 25 (5/20) |
| keycloak/keycloak | 1e3472b7 | Java (+TS) | 10,497 | 25 (5/20) |
| apache/airflow | c951918e | Python (+TS) | 9,607 | 25 (5/20) |
| angular/angular | 87d38ec3 | TypeScript | 9,176 | 25 (5/20) |

Gold languages across the 149 tasks:

| Language | Tasks |
|---|---|
| TypeScript | 42 |
| Rust | 25 |
| C++ | 25 |
| Java | 20 |
| Go | 18 |
| Python | 18 |
| Vue | 1 |

In 37 tasks (`leak_path`) the issue names a gold file. That is a reported stratum.

## Arms

| Arm | What the agent gets |
|---|---|
| A | Built-in Read/Grep/Glob/Bash |
| B | A + CE MCP tools |
| F | B + a one-line "use CE first" instruction |
| H | A + a PreToolUse hook that answers every Grep/Glob/grep with CE's top 5 hits, so the agent never has to choose CE |

Details are in [PREREGISTRATION.md](PREREGISTRATION.md).

## Layout

| Path | Contents |
|---|---|
| `data/repos.json` | Snapshots and excludes |
| `data/tasks.jsonl` | The frozen tasks: issue text, PR/issue URLs, gold files, changed lines, enclosing definitions, leak flags |
| `data/split-{dev,test}.txt` | The dev and test splits |
| `harness/mine_tasks.py` | Mines candidates from GitHub (GraphQL + REST, read-only) |
| `harness/build_tasks.py` | Validates hunks against the snapshot trees, then samples and splits deterministically |
| `harness/prepare.py` | Fetches the snapshot tarballs and indexes them with the CE bridge (`ctxce index`) |
| `harness/freshness.py` | Per-collection routing + gold freshness gate |
| `harness/run.py` | Runs headless Claude Code (`claude -p --output-format stream-json`) per task × arm |
| `harness/ce_hook.cjs` | The arm-H hook |
| `harness/score.py` | Trace scorer |
| `harness/stats.py` | Paired stats: bootstrap CI, sign-flip permutation p, Holm across arms |
| `results/` | Per-run metric records and the tables |

## Reproduce

```
export GH_TOKEN=...            # read-only GitHub API
python3 harness/mine_tasks.py go-gitea/gitea ...; python3 harness/build_tasks.py
python3 harness/prepare.py index go-gitea/gitea ...; python3 harness/freshness.py
python3 harness/run.py --split dev --arms A,B,F,H --model haiku55 --jobs 12
python3 harness/stats.py --split dev --model haiku55 --out results/dev-haiku55.md
```

Credentials stay out of the repo:

- Bedrock auth is read from the environment or `~/.claude/settings.json`.
- The CE session is read from `~/.ctxce/auth.json` by the proxy and the hook, which send it only as a request
  header.

The issue text in `data/` is public GitHub content, quoted with links to the source. No repository code is
redistributed.

License: Apache-2.0 (harness and data layout).
