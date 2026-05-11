# Changelog

## v0.23.0 - 2026-05-12

### New Features

- Per-agent review models: `review.<agent>.model` in `.cadence/config.yaml` (or per-task `config.yaml`) overrides the Task-tool model alias for each review sub-agent (`quality`, `implementation`, `testing`, `simplification`, or any user-defined agent under `.cadence/agents/`). Allowed values: `opus`, `sonnet`, `haiku`. Resolution order (highest wins): per-task YAML → top-level YAML → frontmatter in `.cadence/agents/<name>.txt` → frontmatter in the embedded default → empty (Task tool invoked without `model=`).
- Separate squash-phase model: new `squash.model` (or top-level `squash_model:`) config key, default `claude-sonnet-4-6`. `cadence squash` no longer reuses `task_model`; usage and cost reporting for the squash phase use `squash_model` too.

### Other

- Default model for the `quality` and `implementation` review sub-agents is now explicitly `sonnet` (previously inherited the session default — in practice `opus` when running `cadence run` on opus); `testing` and `simplification` keep `opus` explicitly. To restore the previous behavior set `review.quality.model: opus` and `review.implementation.model: opus`.

## v0.22.0 - 2026-05-06

### New Features

- `cadence chain --parallel N` runs up to N tasks concurrently, each in its own git worktree, with fail-fast on the first failure; per-task diff stats, the post-plan "run: cadence task" hint, and squash "nothing to squash" lines are suppressed in parallel mode to keep main stdout clean (the per-task progress files still capture each task's run); plan-phase questions abort that task with a re-run-sequentially hint; `cadence chain` now rejects duplicate task names up front.
- `cadence status` reports the current branch's task (file presence, last commit outside `tasks_root`, state) and lists other tasks grouped by liveness; `--current` skips the cross-task listing; `--json` produces machine-readable output; `running_threshold_minutes` (default 10) tunes the in-flight classification.
- `cadence doctor` runs environment, repository, config, prompts, agents, hooks, and context diagnostics, prints a Rich-styled report, honors `--config`, and exits non-zero if any check fails.
- `cadence report api-changes` generates a markdown report of public-API differences vs the default branch (or `--base <branch>`), writing it to `<tasks_root>/<branch>/report-api-changes.md` and echoing to stdout; `--stdout-only` suppresses the file write; scope is taken from `public_api_paths` in config or inferred from the repo; optional `.cadence/context/*` files are folded in for extra context; the run aborts with exit 2 on a detached HEAD or when invoked on the default branch.
- `cadence report test-cases` generates a manual-QA test-case report for the current branch against its base (`--base <branch>` overrides the default; `--stdout-only` suppresses the file write); per-phase model overrides are accepted via `report_test_cases_model` (and `report_api_changes_model`).
- `cadence init --template <name>` pre-fills the scaffolded init file from `.cadence/templates/<name>.txt` with `{{task_name}}`, `{{branch}}`, `{{date}}`, and `{{author}}` substitution; invalid template names or missing templates exit 2 with no side effects; the templates directory is configurable via `templates_dir`.
- Lifecycle hooks: `.cadence/hooks/{pre,post}-{plan,task,review,squash,report}.sh` fire at every phase boundary and receive `CADENCE_*` env vars (`CADENCE_PHASE`, `CADENCE_BRANCH`, `CADENCE_TASK_NAME`, `CADENCE_TASKS_ROOT`, `CADENCE_REPORT_TYPE`, `CADENCE_HOOK`, plus post-only `CADENCE_PHASE_RESULT` and `CADENCE_PHASE_DURATION_MS`); a non-zero pre-hook aborts the phase, a timeout surfaces as exit 124; `hooks_dir`, `hooks_timeout_seconds`, and `hooks_enabled` are configurable.
- Token-usage and approximate-cost summaries are printed for each iteration, at the end of plan/task/review/report phases, and as a grand total after `cadence chain` runs; gated by `print_usage` and `cost_estimates` config flags (both default true).
- `cadence plan --import <path>` and `cadence run plan --import <path>` fold an external brief into the plan prompt (alone for `cadence plan`, alongside the branch's `init` file for `cadence run plan`); `import_max_bytes` (default 256 KiB) bounds the brief size.
- Opt-in `progress_jsonl` config flag emits a structured JSON-Lines progress sink (`phase_start`, `iteration_start`, `iteration_end`, `signal`, `error`, `phase_end` events) alongside the existing text progress log for plan, task, and review runs.

### Changed

- Default `commit_format` reverted to single-line messages (`<branch-name>. <Clause>: <what>.`) with clauses joined by `. ` and items inside a clause by `; `, instead of the previous subject + blank line + body shape.

### Other

- New config keys: `templates_dir`, `print_usage`, `cost_estimates`, `progress_jsonl`, `running_threshold_minutes`, `import_max_bytes`, `public_api_paths`, `hooks_dir`, `hooks_timeout_seconds`, `hooks_enabled`, `report_api_changes_model`, `report_test_cases_model`.

## v0.21.0 - 2026-05-05

### Breaking changes

CLI reworked from flag style to subcommand style. Old flags removed; no aliases. Migration:

| Old | New |
|---|---|
| `cadence --plan <path>` | `cadence plan <path>` |
| `cadence --task <path>` | `cadence task <path>` |
| `cadence --review` | `cadence review` |
| `cadence --review --base X` | `cadence review --base X` |
| `cadence --task-init <name>` | `cadence init <name>` |
| `cadence --run` | `cadence run` |
| `cadence --run --impl` | `cadence run` (auto-detect) or explicit `cadence run plan && cadence run task` |
| `cadence --plan <path> --impl` | `cadence plan <path> && cadence task <derived-plan>` |
| `cadence --squash` | `cadence squash` |
| `cadence --task <p> --squash` | `cadence task <p> && cadence squash` |
| `cadence --chain <path>` | `cadence chain <path>` |

- `--impl` and `--squash` flags removed. Compose pipelines with shell `&&`.
- `cadence run` no longer auto-chains into squash after `plan-completed`; squash is always an explicit step.
- Exit codes now follow git: `0` success, `1` runtime failure, `2` misuse (bad paths, invalid repo state, missing arguments).

### Changed

- Shell completion enabled (`cadence --install-completion` / `--show-completion`) — useful now that subcommands replace flags.
- `cadence <subcommand> --help` provides per-command help.

## v0.20.0 - 2026-05-05

### New Features

- `cadence --task-init <task-name>` scaffolds a new branch and task directory under `tasks_root` without invoking Claude.
- `cadence --run` infers the current branch's task directory and dispatches to plan creation, plan execution, or "already completed". `--run --impl` chains the init → plan → task pipeline end-to-end.
- `cadence --squash` squashes all branch commits into one with a Claude-authored message summarizing the diff against the default branch. Chains automatically after `--task`, `--plan --impl`, and `--run --impl`.
- `cadence --chain <path>` runs an ordered list of tasks from a file, each on its own branch as `--run --impl --squash`, branched off each task's resolved `default_branch`. Fails fast and stops on the offending branch.
- Plan template now requires Accepted Trade-offs and Out of Scope sections; review skips findings covered by accepted trade-offs (except bugs, security, data loss, missing error-path tests, failing tests/linter, regressions).
- `cadence --review` auto-discovers an existing plan next to the current branch and feeds it to the reviewer.
- Configurable `init_prompt_name` in `.cadence/config.yaml` (default `init`) — controls which prompt file name maps to "plan" when deriving the plan path.

### Fixes

- Signal markers embedded in `tool_result` content no longer trigger false signal detection.

### Other

- Project docs under `docs/` rewritten in English and resynced with the code; README adds CI badge and Homebrew/PyPI install instructions.
- GitHub Actions CI runs ruff lint, ruff format check, mypy, and pytest on push to `main` and every PR.
- Default `commit_format` shipped with cadence now uses the multi-line subject + body shape so GitHub PRs auto-fill title and description from commits.
- Task prompt no longer instructs the agent to commit the plan file (plan lives under gitignored `tasks_root`).

## v0.19.2 - 2026-05-02

First public release. Available via `pip install cadence-runner` and `brew tap Drozdetskiy/cadence && brew install cadence`.

### New Features

- Three-mode CLI: `--plan`, `--task`, `--review`.
- `--impl` flag chains task execution after `--plan`.
- Multi-agent code review: 4 parallel agents (quality, implementation, testing, simplification) with review-loop until clean.
- Per-phase model configuration via `.cadence/config.yaml`.
- Configurable `commit_format` and `tasks_root` in `.cadence/config.yaml`.
- `--base` and `--config` CLI flags.
- Idle/session timeouts; break/resume via SIGINT/SIGQUIT.
