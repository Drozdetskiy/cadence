# cadence

Autonomous task-execution pipeline on top of [Claude Code](https://docs.anthropic.com/en/docs/claude-code).

`cadence` drives Claude through a structured loop: plan → branch → iterative implementation → multi-agent code review → review-loop until clean → optional finalize. It is a thin orchestrator — Claude does the work, `cadence` keeps it on rails (signals, retries, idle/session timeouts, break/resume, per-phase models, git integration).

## What it does

| Stage | Trigger | What happens |
|---|---|---|
| **plan** | `cadence --plan <file>` | Interactive Q&A with Claude, draft review (accept / revise / reject), final plan written to `<file>-plan.md` |
| **task** | `cadence --task <plan>` | Branch created from plan filename, one `### Task N:` section per iteration, each completed and committed |
| **review** | implicit after `--task`, or `cadence --review` | First pass launches 4 parallel agents (quality, implementation, testing, simplification); subsequent passes loop on critical/major findings until no commits are produced |
| **finalize** | `finalize_enabled: true` in config | Optional best-effort wrap-up step |

Phases communicate with the runner via signal markers (e.g. `<<<CADENCE:PLAN_READY>>>`, `<<<CADENCE:ALL_TASKS_DONE>>>`, `<<<CADENCE:REVIEW_DONE>>>`, `<<<CADENCE:QUESTION>>>`, `<<<CADENCE:TASK_FAILED>>>`).

## Installation

```bash
# from source, editable
pip install -e .

# or with pdm
pdm install
```

Requires:
- Python **3.14+**
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) on `PATH`
- a git repository (the `--task` and `--review` modes operate on the working tree)

## Usage

```bash
# 1. Create a plan from a free-form task description
cadence --plan tasks/my-feature-prompt.md
#   → writes tasks/my-feature-plan.md

# 2. Create a plan and chain straight into implementation
cadence --plan tasks/my-feature-prompt.md --impl

# 3. Execute an existing plan: branch + tasks + review + finalize
cadence --task tasks/my-feature-plan.md

# 4. Review the current branch only (no plan, no branch creation)
cadence --review
cadence --review --base develop          # override base branch
cadence --review --config overrides.yaml # per-run model overrides

# Misc
cadence --version
```

Flag rules:
- `--plan`, `--task`, `--review` are mutually exclusive.
- `--impl` requires `--plan` (and is incompatible with `--review`).
- `--base` is only valid with `--review`. Resolution priority: `--base` > `default_branch` in config > git auto-detect.

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
- After a successful `--task` run the file is renamed in place to `<stem>-completed<ext>` (no commit — plan files are typically gitignored).

## Configuration

### Local config: `.cadence/config.yaml`

Project-scoped, no global config. Loaded from cwd (or `CADENCE_CONFIG_DIR`). All keys are optional — missing keys fall back to defaults.

```yaml
# Claude executor
claude_command: claude
claude_args: "--dangerously-skip-permissions --output-format stream-json --verbose"
plan_model:   claude-opus-4-7
task_model:   claude-opus-4-7
review_model: claude-opus-4-7

# Iteration / timing
iteration_delay_ms: 2000
task_retry_count:   1
max_iterations:     50
session_timeout:    "0"   # "30m", "1h30m", … 0 = disabled
idle_timeout:       "0"
wait_on_limit:      "0"   # >0 → retry on rate-limit instead of failing

# Feature flags
finalize_enabled: false

# VCS / paths
plans_dir:      docs/plans
default_branch: ""        # auto-detect when empty
vcs_command:    git
commit_trailer: ""        # appended to all cadence-made commits

# Output
colors:
  task: "#2e8b57"
  review: "#1a9e9e"
  warn: "#d4930d"
  error: "#cc0000"
```

See [`docs/config.md`](docs/config.md) for the full key reference (timeouts, error patterns, color palette).

### Per-run model overrides: `--config`

`--config <path>` loads a YAML file that overrides only the per-phase models. Each section is optional:

```yaml
plan:
  model: claude-opus-4-7
task:
  model: claude-opus-4-7
review:
  model: claude-opus-4-7
```

If `--config` is omitted, cadence auto-discovers `cadence-config.yaml` next to the plan/task file (no parent walk). For `--review` (no plan/task file) auto-discovery is skipped — only an explicit `--config` is honored. An explicit path that does not exist is a hard error; an auto-discovered missing file is silently ignored. YAML parse errors are always fatal.

### Customizing prompts and agents

`cadence` ships with embedded defaults under `src/cadence/defaults/`. To customize, drop replacements into the project:

```
.cadence/
  config.yaml
  prompts/
    make_plan.txt        # overrides plan-creation prompt
    task.txt             # overrides task-iteration prompt
    review_first.txt     # overrides initial review prompt
    review_second.txt    # overrides review-loop prompt
    finalize.txt
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

## Runtime controls

- **Ctrl+C** — graceful shutdown (twice within 5s force-exits).
- **Ctrl+\\** (`SIGQUIT`, Unix) — break the current task; the runner kills the active Claude session and prompts to resume or abort. Resume restarts the same task with a fresh session and re-reads the plan file.
- **Rate limits** — if `wait_on_limit > 0` and Claude output matches `claude_limit_patterns`, cadence sleeps and retries indefinitely until cancellation.
- **Session / idle timeouts** — kill stuck sessions; review-loop iterations skip the no-commit detection if the previous session timed out.

## Project layout

```
src/cadence/
  cli.py            Typer entrypoint, mode dispatch, signal handling
  config.py         Config dataclass, YAML loading, --config overrides
  status.py         Phase / Mode / Signal constants
  input.py          Interactive Q&A collector
  executor/         Claude subprocess + JSON-stream parsing
  git/              Service layer over `git` CLI
  plan/             Markdown plan parser, branch-name extraction
  processor/        Runner — orchestrates plan/task/review/finalize phases
  progress/         File+stdout logger with colors and flock
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