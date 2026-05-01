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