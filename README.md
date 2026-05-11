# cadence

[![PyPI version](https://img.shields.io/pypi/v/cadence-runner.svg)](https://pypi.org/project/cadence-runner/) [![Python versions](https://img.shields.io/pypi/pyversions/cadence-runner.svg)](https://pypi.org/project/cadence-runner/) [![License](https://img.shields.io/pypi/l/cadence-runner.svg)](LICENSE) [![CI](https://github.com/Drozdetskiy/cadence/actions/workflows/ci.yml/badge.svg)](https://github.com/Drozdetskiy/cadence/actions/workflows/ci.yml) [![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-261230.svg)](https://github.com/astral-sh/ruff)

Autonomous task-execution pipeline on top of [Claude Code](https://docs.anthropic.com/en/docs/claude-code).

Inspired by the [ralphex](https://ralphex.com/) project.

`cadence` drives Claude through a structured loop: plan → branch → iterative implementation → multi-agent code review → review-loop until clean. It is a thin orchestrator — Claude does the work, `cadence` keeps it on rails (signals, retries, idle/session timeouts, break/resume, per-phase models, git integration).

## What it does

| Subcommand | What happens |
|---|---|
| `cadence init <name> [--template <t>]` | Scaffolds a new branch + `<tasks_root>/<name>/init` (plus `config.yaml` recording the parent branch when not on `default_branch`). With `--template <t>`, pre-fills `init` from `.cadence/templates/<t>.txt` (variables `{{task_name}}`, `{{branch}}`, `{{date}}`, `{{author}}`). No Claude. |
| `cadence run` | Infers the current branch's task directory and dispatches to the next phase (`run plan` if only `init` exists, `run task` if `plan` exists, "already completed" if `plan-completed` exists). |
| `cadence run plan [--import <brief>]` | Branch-bound plan creation: reads `<tasks_root>/<branch>/init` and writes `plan` next to it. With `--import <brief>`, folds the brief in under a `# External brief` heading alongside `init`. |
| `cadence run task` | Branch-bound task execution: reads `<tasks_root>/<branch>/plan` and runs the full pipeline (branch already exists). |
| `cadence plan <path> [--import]` | Path-bound plan creation from an arbitrary file: interactive Q&A with Claude, draft review, final plan written to `<path>-plan.md` (or `plan` next to `init`). With `--import`, treats `<path>` itself as the external brief (no `init` content). |
| `cadence task <path>` | Path-bound task execution on an arbitrary plan: branch created from plan filename, one `### Task N:` section per iteration, each completed and committed. |
| `cadence review [--base <branch>]` | Review-only of the current branch: first pass launches 4 parallel agents (quality, implementation, testing, simplification); subsequent passes loop on critical/major findings until no commits are produced. |
| `cadence squash` | Squashes all branch commits into one with a Claude-authored message summarizing the diff against the default branch. |
| `cadence chain <path> [--parallel N]` | Reads an ordered list of task names from a file and runs each task end-to-end (plan → task → squash) on its own branch. Pre-flight checks every listed task has a directory + `init` file and rejects duplicate names; fails fast. With `--parallel N`, runs up to `N` tasks concurrently in isolated git worktrees under `.cadence/worktrees/<task-name>`. |
| `cadence status [--current] [--json]` | Lists task directories under `tasks_root` with their state (`init only`, `plan ready`, `in flight`, `completed`) and last-activity age. `--current` restricts to the current branch's task; `--json` emits machine-readable output. |
| `cadence doctor` | Pre-flight diagnostics: environment, repository, config, prompts, agents, hooks, and context checks. No Claude calls. Exits non-zero if any check fails. |
| `cadence report api-changes [--base <b>] [--stdout-only]` | Generates a public-API-changes report for the current branch. Writes to `<tasks_root>/<branch>/report-api-changes.md` (or stdout with `--stdout-only`). Honours `public_api_paths` to constrain the diff and folds in `.cadence/context/*` when present. |
| `cadence report test-cases [--base <b>] [--stdout-only]` | Generates a manual-QA test-case report for the current branch. Writes to `<tasks_root>/<branch>/report-test-cases.md` (or stdout with `--stdout-only`). |

Compose pipelines with shell `&&` — e.g. `cadence run && cadence run && cadence squash` to take a fresh task from `init` through plan, task, and squash.

Phases communicate with the runner via signal markers (e.g. `<<<CADENCE:PLAN_READY>>>`, `<<<CADENCE:ALL_TASKS_DONE>>>`, `<<<CADENCE:REVIEW_DONE>>>`, `<<<CADENCE:QUESTION>>>`, `<<<CADENCE:TASK_FAILED>>>`).

## Installation

```bash
# via Homebrew (macOS)
brew tap drozdetskiy/cadence
brew install drozdetskiy/cadence/cadence  # tap-qualified name avoids the homebrew-core `cadence` (Flow smart-contract language) clash

# via PyPI
pip install cadence-runner

# from source (for development)
pdm install
```

The PyPI distribution is named `cadence-runner`; the CLI binary is `cadence` regardless of how you installed it.

Requires:
- Python **3.14+** (Homebrew formula pulls this in automatically)
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) on `PATH`
- a git repository (the `task`, `run`, `review`, `squash`, and `chain` subcommands operate on the working tree)

Note: the `cadence init <name>` subcommand and the file named `init` inside the task directory are different things — `init` (subcommand) scaffolds a task, and `init` (file) is the free-form description Claude reads. The file name is configurable via `init_prompt_name` in `.cadence/config.yaml`.

## Usage

Tasks live in their own subdirectory under `cdc-tasks/<NNNN-slug>/` (configurable via `tasks_root`). The free-form description goes into a file named `init`; the generated plan is written next to it as `plan` (the `init` → `plan` mapping is configurable via `init_prompt_name`). A per-task `config.yaml` next to the prompt is auto-discovered.

```
cdc-tasks/
  0001-my-feature/
    init               # free-form task description (input)
    plan               # generated by `cadence run plan`, consumed by `cadence run task`
    config.yaml        # optional per-task overrides (auto-discovered)
```

```bash
# New task end-to-end (branch-bound, with auto-detect)
cadence init 0001-my-feature                  # scaffolds branch + cdc-tasks/0001-my-feature/init
$EDITOR cdc-tasks/0001-my-feature/init        # write the task description
cadence run && cadence run && cadence squash  # auto-detect: init → plan, plan → task, then squash

# Same flow, explicit phases
cadence run plan                              # init → plan
cadence run task                              # plan → task pipeline (branch + tasks + review)
cadence squash                                # one final commit summarizing the branch

# Pre-fill init from a saved template (.cadence/templates/<name>.txt)
cadence init 0002-bugfix --template bugfix    # substitutes {{task_name}}, {{branch}}, {{date}}, {{author}}

# Plan from a third-party file (path-bound)
cadence plan path/to/spec.md                  # writes path/to/spec-plan.md (or `plan` next to `init`)

# Plan with an external brief folded in
cadence plan path/to/brief.md --import        # path-bound: brief is the only input
cadence run plan --import path/to/brief.md    # branch-bound: brief alongside init

# Task on an existing plan (path-bound, creates the branch)
cadence task path/to/plan.md

# Review the current branch only (no plan, no branch creation)
cadence review
cadence review --base develop                 # override base branch

# Branch status across tasks_root
cadence status                                # phase + last-touched per task
cadence status --current                      # only the current branch
cadence status --json                         # machine-readable

# Pre-flight diagnostics (env, repo, config, prompts, agents, hooks, context)
cadence doctor                                # exits non-zero on any failed check

# Branch-scoped reports (Claude side is read-only; no git commits — cadence writes the report file)
cadence report api-changes                    # writes cdc-tasks/<branch>/report-api-changes.md
cadence report api-changes --stdout-only      # print instead of writing
cadence report test-cases --base develop      # manual-QA test cases vs. develop

# Per-run overrides apply globally to any subcommand
cadence --config cdc-tasks/0001-my-feature/config.yaml run

# Chain of tasks (sequential plan → task → squash per task, each on its own branch)
cadence chain tasks.txt

# Parallel chain (each task runs in its own git worktree under .cadence/worktrees/)
cadence chain tasks.txt --parallel 4

# Misc
cadence --version
cadence --help
cadence run --help
```

Migration from the old flag-style CLI:

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

### Plan file format

Plans are markdown with a strict structure the runner parses on every iteration:

```markdown
# Title

## Overview
…

## Context
- Files involved: …

### Task 1: Setup
- [ ] step one
- [ ] step two
- [ ] write tests
- [ ] run test suite

### Task 2: …
- [ ] …
```

- `### Task N:` or `### Iteration N:` headers delimit work units.
- `- [ ]` / `- [x]` checkboxes inside Task sections drive progress.
- Checkboxes outside Task sections (Overview, Context, Success criteria) do not block completion.
- After a successful `task`/`run task` run the file is renamed in place to `<stem>-completed<ext>` (no commit — plan files are typically gitignored).

## Configuration

### Local config: `.cadence/config.yaml`

Project-scoped, no global config. Loaded from cwd (or `CADENCE_CONFIG_DIR`). All keys are optional — missing keys fall back to defaults.

```yaml
# Claude executor
claude_command: claude
claude_args: "--dangerously-skip-permissions --verbose"
plan_model:                claude-opus-4-7
task_model:                claude-opus-4-7
review_model:              claude-opus-4-7
squash_model:              claude-sonnet-4-6
report_api_changes_model:  ""           # empty = fall back to review_model
report_test_cases_model:   ""           # empty = fall back to review_model

# Iteration / timing
iteration_delay_ms: 2000
task_retry_count:   1
max_iterations:     50
session_timeout:    "0"   # "30m", "1h30m", … 0 = disabled
idle_timeout:       "5m"  # default 5m; "0" disables
wait_on_limit:      "0"   # >0 → retry on rate-limit instead of failing

# VCS / paths
tasks_root:     cdc-tasks # root for per-task subdirectories (init/plan/config.yaml)
default_branch: main      # override per-project in local config
commit_trailer: ""        # appended to all cadence-made commits
commit_format:  |         # appended to task/review prompts (default shown below)
  Format: a single line `<branch-name>. <Clause>: <what>.` where `<Clause>`
  is `Added`, `Changed`, or `Deleted`. Clauses joined by `. ` (period + space);
  items inside one clause joined by `; ` (semicolon + space). ...

# Reports
public_api_paths: []      # default empty = whole diff; e.g. ["src/cadence/cli.py", "src/cadence/config.py"]

# Lifecycle hooks
hooks_dir:             .cadence/hooks   # location of pre/post-<phase>.sh scripts
hooks_timeout_seconds: 60               # per-hook timeout; on timeout exits 124
hooks_enabled:         true             # set false to skip all hooks

# Init templates
templates_dir: .cadence/templates       # location of `cadence init --template <name>` files

# Plan import
import_max_bytes: 262144                # 256 KiB ceiling on `--import` brief size

# Output / observability
print_usage:    true                    # per-iteration / per-phase token-usage summaries
cost_estimates: true                    # add approximate $ cost to usage summaries
progress_jsonl: false                   # also emit a JSON-Lines progress sink alongside the .txt log

# `cadence status`
running_threshold_minutes: 10           # plan still being worked on if touched within last N minutes

# Output
colors:
  task: "#2e8b57"
  review: "#1a9e9e"
  warn: "#d4930d"
  error: "#cc0000"
```

See [`docs/config.md`](docs/config.md) for the full key reference (timeouts, error patterns, color palette).

### Per-run overrides: `--config`

The global `--config <path>` (placed before the subcommand: `cadence --config X.yaml run`) loads a YAML file that overrides per-phase models and/or `default_branch`. Each key is optional:

```yaml
plan:
  model: claude-opus-4-7
task:
  model: claude-opus-4-7
review:
  model: claude-opus-4-7
  quality:
    model: sonnet     # per-agent override for the review-phase sub-agents
  implementation:
    model: sonnet     # allowed values: opus | sonnet | haiku (Task-tool aliases)
  testing:
    model: opus
  simplification:
    model: opus
squash:
  model: claude-sonnet-4-6
report_api_changes:
  model: claude-opus-4-7
report_test_cases:
  model: claude-opus-4-7
default_branch: develop
```

Per-agent review models (`review.<agent>.model`) override the Task-tool model alias for each review sub-agent independently of `review.model` (which is the model the top-level reviewer runs on). Values must be one of `opus`, `sonnet`, `haiku` — the Task tool only accepts those three aliases. Invalid values raise a `ValueError` at config parse time. Resolution order (highest wins): per-task `config.yaml` → top-level `.cadence/config.yaml` → frontmatter in `.cadence/agents/<name>.txt` → frontmatter in the embedded default agent. Embedded defaults: `quality=sonnet`, `implementation=sonnet`, `testing=opus`, `simplification=opus`.

`squash.model` is a full Claude model ID (like `task_model`/`plan_model`), not an alias — it picks the model `cadence squash` uses to write the commit message. Default `claude-sonnet-4-6`.

If `--config` is omitted, cadence auto-discovers `config.yaml` next to the plan/task file — typically `cdc-tasks/<NNNN-slug>/config.yaml` (no parent walk). For `squash` and `cadence report …` the same lookup is anchored on the current branch's task directory (`cdc-tasks/<branch>/config.yaml`). For `review`, `init`, `status`, `doctor`, and `chain` auto-discovery is skipped — only an explicit `--config` is honored. An explicit path that does not exist is a hard error; an auto-discovered missing file is silently ignored. YAML parse errors are always fatal.

### Commit message format

`commit_format` is appended verbatim to every task and review prompt, telling Claude how to write the commit message. Plan creation does not commit, so the format is not added there.

The built-in default produces messages like:

```
0014-no-plan-commit-on-start. Changed: cadence no longer auto-commits the plan file when starting a task. Deleted: now-unused commit_plan_file; file_has_changes helpers.
```

Shape: a single line `<branch-name>. <Clause>: <what>.` where `<Clause>` is `Added`, `Changed`, or `Deleted`. Multiple clauses are joined by `. ` (period + space); multiple items inside one clause are joined by `; ` (semicolon + space). Include only the clauses that apply.

Override from `.cadence/config.yaml` with any free-form text. Example of a tighter restatement (the shipped default also includes Good/Bad examples and guidance about implementation details belonging in the diff — see `Config.commit_format` in `src/cadence/config.py` for the verbatim text):

```yaml
commit_format: |
  Format: a single line `<branch-name>. <Clause>: <what>.` where `<Clause>` is
  `Added`, `Changed`, or `Deleted`. Clauses joined by `. ` (period + space);
  items inside one clause joined by `; ` (semicolon + space). English.
  Each clause is one short item describing the user-visible outcome.
  Author as the user — no Co-Authored-By trailer (unless `commit_trailer` is configured).
```

Switch to Conventional Commits:

```yaml
commit_format: |
  Use Conventional Commits: <type>(<scope>): <subject>
  Types: feat, fix, refactor, docs, test, chore.
  Subject is imperative, lowercase, no trailing period, ≤72 chars.
  Example: feat(executor): add idle-timeout retry
```

If you need finer control than a free-form block (e.g. different wording per phase), drop a custom `task.txt` / `review_first.txt` / `review_second.txt` under `.cadence/prompts/` — the format block is appended to whatever prompt body you supply.

### Customizing prompts and agents

`cadence` ships with embedded defaults under `src/cadence/defaults/`. To customize, drop replacements into the project:

```
.cadence/
  config.yaml
  prompts/
    make_plan.txt          # overrides plan-creation prompt
    task.txt               # overrides task-iteration prompt
    review_first.txt       # overrides initial review prompt
    review_second.txt      # overrides review-loop prompt
    squash_commit.txt      # overrides squash-commit message prompt
    report_api_changes.txt # overrides cadence report api-changes prompt
    report_test_cases.txt  # overrides cadence report test-cases prompt
  agents/
    quality.txt          # custom review agents (referenced as {{agent:quality}})
    implementation.txt
    testing.txt
    simplification.txt
    my-extra-agent.txt   # add new agents — auto-discovered
```

Per-file fallback: if a local file is empty or contains only `# comments`, the embedded default is used. Agents support optional YAML frontmatter:

```
---
model: sonnet         # haiku | sonnet | opus (or full IDs, normalized)
agent: code-reviewer  # subagent type for the Task tool
---
Agent prompt body…
```

Prompts can reference agents inline with `{{agent:name}}`; the runner expands these into full Task tool invocations, with base variables (`{{PLAN_FILE}}`, `{{DEFAULT_BRANCH}}`, etc.) substituted into the agent body.

## Feature guides

### Status overview (`cadence status`)

Reach for `cadence status` when you want a quick read on every task directory under `tasks_root` — what phase each one is in (`init only`, `plan ready`, `in flight`, `completed`), when it was last touched, and what the current branch's task looks like in particular. Use `--current` to skip the table of other tasks; use `--json` to script against the output. A task is reported as `in flight` only while its `progress-plan.txt` / `progress-task.txt` was modified within the last `running_threshold_minutes` window (default 10) and the progress log has not yet printed a "Completed:" terminator — older or terminated runs flip back to `plan ready` / `completed`.

```bash
cadence status                                # current branch + table of other tasks
cadence status --current                      # only the current branch's task
cadence status --json                         # machine-readable: {tasks_root, current, tasks: [...]}
```

### Pre-flight diagnostics (`cadence doctor`)

`cadence doctor` runs seven categories of checks — `environment` (Claude on PATH, `git` on PATH, Python version), `repository` (cwd is a git repo, default branch resolvable), `config` (`.cadence/config.yaml` parses, durations are valid), `prompts` (any local overrides under `.cadence/prompts/` parse), `agents` (any local agent files parse), `hooks` (scripts under `hooks_dir` are present and executable), and `context` (`.cadence/context/*` files have allowed extensions and fit the byte budget). It does not call Claude. The exit code is non-zero if any check is `fail`.

```bash
cadence doctor                                # prints one line per check; exits 1 on any failure
```

### Lifecycle hooks

Drop executable shell scripts under `.cadence/hooks/` (configurable via `hooks_dir`) named `pre-<phase>.sh` or `post-<phase>.sh` to run before or after each phase. Phases are `plan`, `task`, `review`, `squash`, and `report`. Every hook sees `CADENCE_PHASE`, `CADENCE_BRANCH`, `CADENCE_TASK_NAME`, `CADENCE_TASKS_ROOT` (absolute path), `CADENCE_REPORT_TYPE` (only set for the report phase, otherwise empty), and `CADENCE_HOOK` (`pre` or `post`). Post-hooks additionally see `CADENCE_PHASE_RESULT` (`success` or `failure`) and `CADENCE_PHASE_DURATION_MS`. A non-zero exit from a `pre-` hook aborts the phase with the hook's exit code; a timeout (default 60s, configurable via `hooks_timeout_seconds`) surfaces as exit code 124. Set `hooks_enabled: false` to skip all hooks without removing the files.

```bash
# .cadence/hooks/post-task.sh — notify Slack when a task finishes
#!/usr/bin/env bash
set -euo pipefail
curl -sS -X POST -H 'Content-Type: application/json' \
  --data "{\"text\":\":white_check_mark: ${CADENCE_BRANCH} ${CADENCE_PHASE_RESULT} in ${CADENCE_PHASE_DURATION_MS}ms\"}" \
  "$SLACK_WEBHOOK_URL"
```

### Token-usage and cost summaries

After every Claude iteration cadence prints a one-line summary; after each phase it prints a phase total; `cadence chain` adds a grand total across tasks. Lines look like `iter 3 done in 1m 12s · in 1.2k · out 4.5k · cache_read 12k · cost ≈ $0.18 · session abc-123`, `phase task done in 5m 6s · iters 4 · in 5k · out 12k · cache_read 80k · cost ≈ $0.71`, and `chain done in 22m · tasks 3 · iters 11 · in 15k · out 40k · cache_read 240k · cost ≈ $2.10`. Costs are estimated locally from the pricing table in `src/cadence/usage.py` (Opus / Sonnet / Haiku 4.x) using the per-iteration token counts and the active phase model — they are approximate, not Claude's authoritative bill. Disable per-line summaries with `print_usage: false`, or keep summaries but drop the cost segment with `cost_estimates: false`.

```yaml
# .cadence/config.yaml — quiet down the per-iteration noise
print_usage: false
cost_estimates: false
```

### Reports (`cadence report api-changes` / `cadence report test-cases`)

Use `cadence report api-changes` to summarize the public-API surface a branch changes (good for PR descriptions and release notes); use `cadence report test-cases` to draft a manual-QA test plan for the same diff. Both write to `<tasks_root>/<branch>/report-<type>.md` and append `.cadence/context/*` files (allowed extensions: `.md`, `.txt`, `.sql`, `.yaml`, `.yml`, `.json`, `.proto`; 200,000-byte total budget) when present. `--base <branch>` overrides the diff base; `--stdout-only` skips writing the file. For `api-changes`, set `public_api_paths` to constrain the diff to specific files (empty = whole diff). Detached HEAD or running on the default branch exits 2.

```bash
cadence report api-changes                    # writes cdc-tasks/<branch>/report-api-changes.md
cadence report api-changes --stdout-only      # print to stdout instead
cadence report test-cases --base develop      # diff against develop, write report-test-cases.md
```

```yaml
# .cadence/config.yaml — only treat these as public API
public_api_paths:
  - src/cadence/cli.py
  - src/cadence/config.py
```

### Parallel chain runs (`cadence chain --parallel N`)

`cadence chain tasks.txt --parallel N` runs up to `N` tasks at once, each in its own git worktree under `.cadence/worktrees/<task-name>` so there is no working-tree contention between them. Cadence pre-flights the chain file (every name must already have a directory + `init` under `tasks_root`; duplicate names are rejected; existing branches or worktree paths abort with exit 2) before launching anything. The pool is fail-fast: a task that fails cancels any not-yet-started tasks and the command exits 1 once running tasks finish. Plan-phase questions cannot be answered in parallel mode — if Claude raises a `<<<CADENCE:QUESTION>>>` during plan, the task aborts with `plan requires interactive input; cannot run plan-phase questions in --parallel mode (re-run this task sequentially: cadence run plan)`.

```
# tasks.txt
0010-add-search
0011-add-filters
0012-add-sort
```

```bash
cadence chain tasks.txt --parallel 2          # run 2 tasks at a time, each in its own worktree
```

### JSON-Lines progress sink (`progress_jsonl`)

Set `progress_jsonl: true` in `.cadence/config.yaml` to emit a structured `.jsonl` event log alongside the human-readable `.txt` progress file (e.g. `progress-task.jsonl` next to `progress-task.txt`). Events are: `phase_start`, `phase_end`, `iteration_start`, `iteration_end`, `signal`, and `error`. Each line is a JSON object with at least `ts`, `phase`, and `event`; iteration/phase events also carry `duration_ms`, token counts, and (when known) `cost_usd_estimate`. Useful for piping cadence runs into a dashboard or log aggregator without parsing the colored text log.

```yaml
# .cadence/config.yaml
progress_jsonl: true
```

```bash
cadence run task
tail -f cdc-tasks/0001-my-feature/progress-task.jsonl | jq .
```

### Init templates (`cadence init --template <name>`)

Drop a `.txt` file under `.cadence/templates/` (configurable via `templates_dir`) and `cadence init <task> --template <name>` will pre-fill the scaffolded `init` from `.cadence/templates/<name>.txt` instead of leaving it empty. Four substitution variables are expanded: `{{task_name}}`, `{{branch}}` (currently the same as `{{task_name}}`), `{{date}}` (today as `YYYY-MM-DD`), and `{{author}}` (from `git config user.name`, empty if unset). A missing template, invalid template name, or other validation failure exits 2 with no side effects.

```
# .cadence/templates/bugfix.txt
# Bug: {{task_name}}

Branch: {{branch}}
Date:   {{date}}
Author: {{author}}

## Repro steps
1. …

## Expected vs. actual
…
```

```bash
cadence init 0042-fix-login --template bugfix
```

### Plan from an external brief (`cadence plan --import` / `cadence run plan --import`)

When the task description already lives in a separate document (a Linear ticket export, a design doc, an LLM-generated brief), fold it into the plan prompt without copy-pasting:

- `cadence plan <path> --import` is path-bound and treats `<path>` as the brief itself — there is no `init` file alongside it. The plan prompt receives only `# External brief (imported from <path>)` plus the brief body.
- `cadence run plan --import <path>` is branch-bound — the prompt receives the current branch's `init` under `# Task brief (init)` and the imported file under `# External brief (imported from <path>)`, with an explicit precedence note that `init` wins on conflict.

The brief is rejected if larger than `import_max_bytes` (default 256 KiB).

```bash
cadence plan path/to/brief.md --import        # path-bound: brief is the only input
cadence run plan --import path/to/brief.md    # branch-bound: brief alongside init
```

## Runtime controls

- **Ctrl+C** — graceful shutdown (twice within 5s force-exits).
- **Ctrl+\\** (`SIGQUIT`, Unix) — break the current task; the runner kills the active Claude session and prompts to resume or abort. Resume restarts the same task with a fresh session and re-reads the plan file.
- **Rate limits** — if `wait_on_limit > 0` and Claude output matches `claude_limit_patterns`, cadence sleeps and retries indefinitely until cancellation.
- **Session / idle timeouts** — kill stuck sessions; review-loop iterations skip the no-commit detection if the previous session timed out.

## Project layout

```
src/cadence/
  cli.py            Typer entrypoint, subcommand dispatch (init/run/plan/task/review/squash/chain/status/doctor/report), signal handling
  config.py         Config dataclass, YAML loading, --config overrides
  status.py         Phase / Mode / Signal constants
  input.py          Interactive Q&A collector
  hooks.py          Lifecycle hook runner (pre/post-<phase>.sh)
  templates.py      `cadence init --template` loader and variable substitution
  usage.py          Token-usage / cost-estimate aggregation and formatting
  executor/         Claude subprocess + JSON-stream parsing
  git/              Service layer over `git` CLI
  plan/             Markdown plan parser, branch-name extraction
  processor/        Runner — orchestrates plan/task/review phases; report dispatcher
  progress/         File+stdout logger with colors and flock; JSONL event sink
  diagnostics/      `cadence status` and `cadence doctor` implementations
  defaults/
    prompts/        Embedded prompt templates
    agents/         Embedded review agents
```

Deeper module references live in [`docs/`](docs/): `config.md`, `processor.md`, `executor.md`, `git-and-plans.md`, `progress-and-input.md`.

## Development

```bash
make install     # pdm install --dev
make test        # pytest tests/ -v
make test-cov    # with coverage
make lint        # ruff check
make typecheck   # mypy --strict
make check       # lint + typecheck + test
```

Conventions:
- Python 3.14+, `mypy --strict`.
- `Protocol`-based interfaces for all `Runner` dependencies (Executor, Logger, InputCollector, GitChecker) — tests mock these directly, never the real Claude CLI or a real git repo.
- Signals are literal strings of the form `<<<CADENCE:NAME>>>` and matched against raw non-JSON CLI output only (so the same literal inside stream-json events does not trigger false positives).

## License

MIT. See [`LICENSE`](LICENSE).