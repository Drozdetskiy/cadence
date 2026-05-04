# Changelog

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
