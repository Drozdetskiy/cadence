# rlx

A Python CLI tool for autonomous task execution via Claude Code. In v0.1, `rlx` supports interactive plan creation: given a file describing a task, it drives a Q&A dialogue with Claude to produce a detailed implementation plan.

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

# Show version
rlx --version
```

When you run `rlx --plan task.md`, the tool:
1. Reads your task description
2. Starts an interactive dialogue with Claude
3. Asks clarifying questions about your requirements
4. Writes the final plan as `<file>-plan.md` next to the source file

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