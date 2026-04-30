# rlx

A Python CLI tool for autonomous task execution via Claude Code. `rlx` supports interactive plan creation (`--plan`), full task pipelines (`--task`: tasks → review → finalize), and standalone branch review (`--review`).

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
rlx --plan <file>

# Create a plan and queue auto-implementation (v0.2+)
rlx --plan <file> --impl

# Execute a plan: branch creation, iterative task execution, review pass, finalize
rlx --task <file>

# Review the current branch against the default branch (no plan, no tasks)
rlx --review

# Review against an explicit base branch (overrides config default_branch)
rlx --review --base develop

# Override per-step Claude models via YAML (works with --plan, --task, --review)
rlx --plan task.md --config rlx-config.yaml
rlx --task plan.md --config rlx-config.yaml
rlx --review --config rlx-config.yaml

# Show version
rlx --version
```

When you run `rlx --plan task.md`, the tool:
1. Reads your task description
2. Starts an interactive dialogue with Claude
3. Asks clarifying questions about your requirements
4. Writes the final plan as `<file>-plan.md` next to the source file

### `--review`

`rlx --review` runs the review phase against the current branch without creating a plan or executing tasks. It performs a first-pass review, then iterates a critical/major review loop until no further commits are produced (or the iteration cap is reached), and runs the finalize step when `finalize_enabled` is set in config. Set `review_model` in config to use a distinct Claude model for review/finalize. Pass `--base <branch>` to override the base branch used for the review diff for the current run only (resolution priority: `--base` > config `default_branch` > git auto-detect); the flag does not modify `.rlx/config.toml`. `--review` is incompatible with `--impl` and `--plan` / `--task`; `--base` is only valid with `--review`.

### `--config`

`--config <path>` points to an optional YAML file that overrides the per-step Claude model loaded from `.rlx/config.toml`. Available with `--plan`, `--task`, and `--review`. Each section is optional and only overrides the matching TOML default (`plan_model`, `task_model`, `review_model`):

```yaml
task:
  model: claude-opus-4-7
review:
  model: claude-opus-4-7
plan:
  model: claude-opus-4-7
```

When `--config` is omitted, rlx auto-discovers `rlx-config.yaml` in the directory containing the plan/task file (no parent walk). For `--review` (no plan/task file), auto-discovery is skipped — only an explicit `--config` is honored. An explicit path that does not exist is a hard error; an auto-discovered path that is missing is silently ignored. YAML parse errors are always a hard error.

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