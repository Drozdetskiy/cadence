# cadence

A Python CLI tool for autonomous task execution via Claude Code. `cadence` supports interactive plan creation (`--plan`), full task pipelines (`--task`: tasks → review → finalize), and standalone branch review (`--review`).

## Installation

```bash
# Using pdm
pdm install

# Or pip (editable)
pip install -e .
```

## Usage

```bash
# Create an implementation plan from a task description file
cadence --plan <file>

# Create a plan and queue auto-implementation (v0.2+)
cadence --plan <file> --impl

# Execute a plan: branch creation, iterative task execution, review pass, finalize
cadence --task <file>

# Review the current branch against the default branch (no plan, no tasks)
cadence --review

# Review against an explicit base branch (overrides config default_branch)
cadence --review --base develop

# Override per-step Claude models via YAML (works with --plan, --task, --review)
cadence --plan task.md --config cadence-config.yaml
cadence --task plan.md --config cadence-config.yaml
cadence --review --config cadence-config.yaml

# Show version
cadence --version
```

When you run `cadence --plan task.md`, the tool:
1. Reads your task description
2. Starts an interactive dialogue with Claude
3. Asks clarifying questions about your requirements
4. Writes the final plan as `<file>-plan.md` next to the source file

### `--review`

`cadence --review` runs the review phase against the current branch without creating a plan or executing tasks. It performs a first-pass review, then iterates a critical/major review loop until no further commits are produced (or the iteration cap is reached), and runs the finalize step when `finalize_enabled` is set in config. Set `review_model` in config to use a distinct Claude model for review/finalize. Pass `--base <branch>` to override the base branch used for the review diff for the current run only (resolution priority: `--base` > config `default_branch` > git auto-detect); the flag does not modify `.cadence/config.toml`. `--review` is incompatible with `--impl` and `--plan` / `--task`; `--base` is only valid with `--review`.

### `--config`

`--config <path>` points to an optional YAML file that overrides the per-step Claude model loaded from `.cadence/config.toml`. Available with `--plan`, `--task`, and `--review`. Each section is optional and only overrides the matching TOML default (`plan_model`, `task_model`, `review_model`):

```yaml
task:
  model: claude-opus-4-7
review:
  model: claude-opus-4-7
plan:
  model: claude-opus-4-7
```

When `--config` is omitted, cadence auto-discovers `cadence-config.yaml` in the directory containing the plan/task file (no parent walk). For `--review` (no plan/task file), auto-discovery is skipped — only an explicit `--config` is honored. An explicit path that does not exist is a hard error; an auto-discovered path that is missing is silently ignored. YAML parse errors are always a hard error.

## Development

```bash
# Install dev dependencies
pdm install --dev

# Run tests
make test

# Run linter
make lint

# Run type checker
make typecheck

# Run all checks (lint + typecheck + test)
make check

# Test coverage
make test-cov
```

## Requirements

- Python 3.14+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI installed and available in PATH
