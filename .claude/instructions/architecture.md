# Architecture

## Project Overview

rlx — Python CLI tool for autonomous task execution via Claude Code.
Creates plans, executes tasks from plans, runs code review — all through Claude Code CLI.
Python 3.14+, sync + threading (no asyncio).

## Tech Stack

- **CLI**: typer (type hints for CLI, built on click)
- **Color output**: rich (RGB, markdown rendering, terminal formatting)
- **Config**: TOML via tomllib (stdlib, Python 3.11+)
- **Package manager**: pdm
- **Subprocess**: subprocess.Popen (sync, stream-json from claude CLI)
- **Threading**: threading.Timer (idle timeout), threading.Event (cancellation)
- **Embedded resources**: importlib.resources (prompts, agents)
- **Testing**: pytest + ruff + mypy
- **External CLIs**: `claude` (Claude Code), `git`

## Project Structure

```
src/rlx/
├── __init__.py              # version
├── cli.py                   # typer app, entrypoint, signal handling
├── status.py                # Phase, Signal, Section types (leaf module)
├── config.py                # Config dataclass, TOML loading, merge, ColorConfig
├── input.py                 # TerminalCollector: numbered picker, draft review, yes/no
├── git.py                   # Git operations: repo check, default branch, head hash
├── progress/
│   ├── __init__.py
│   ├── logger.py            # Logger: timestamped output, file + stdout, sections
│   ├── colors.py            # Colors: RGB parsing, phase-to-color mapping
│   └── flock.py             # File locking: fcntl (Unix) / no-op (Windows)
├── executor/
│   ├── __init__.py
│   ├── claude_executor.py   # ClaudeExecutor, Result, detect_signal, match_pattern
│   └── process_group.py     # Unix process groups: start_new_session, SIGTERM/SIGKILL
├── processor/
│   ├── __init__.py
│   ├── runner.py            # Runner: dispatches to mode methods, iteration loops
│   ├── prompts.py           # Prompt loading, variable substitution, agent expansion
│   └── signals.py           # parse_question_payload, parse_plan_draft_payload
└── defaults/
    ├── __init__.py
    ├── prompts/
    │   └── make_plan.txt    # Plan creation prompt
    └── agents/              # Review agents (v0.3)

tests/
├── __init__.py
├── test_status.py
├── test_config.py
├── test_input.py
├── test_executor.py
├── test_processor.py
├── test_progress.py
├── test_signals.py
└── test_cli.py
```

## Module Dependency Graph

```
cli.py
  ├── config.py          (load config)
  ├── processor/runner.py (orchestration)
  ├── git.py             (repo validation)
  ├── progress/logger.py (logging)
  └── status.py          (phases, modes)

processor/runner.py
  ├── executor/          (ClaudeExecutor)
  ├── processor/prompts.py
  ├── processor/signals.py
  └── status.py

executor/claude_executor.py
  ├── executor/process_group.py
  └── status.py          (signal detection)

progress/logger.py
  ├── progress/colors.py
  ├── progress/flock.py
  └── status.py          (phases for color mapping)

config.py, status.py, input.py — leaf modules (no internal deps)
```

## Execution Modes

| Mode | CLI flag | Description |
|------|----------|-------------|
| ModePlan | `--plan <file>` | Interactive plan creation via Claude Q&A |
| ModeFull | `--task <file>` | Tasks + review + finalize (v0.2+) |
| ModeReview | `--review` | Review only + finalize (v0.3) |

## Signal Communication

Claude embeds signals in output, rlx detects them via substring search:

| Signal | String | Phase | Meaning |
|--------|--------|-------|---------|
| Completed | `<<<RLX:ALL_TASKS_DONE>>>` | Task | All tasks done |
| Failed | `<<<RLX:TASK_FAILED>>>` | Task/Plan | Task/plan failed |
| ReviewDone | `<<<RLX:REVIEW_DONE>>>` | Review | No findings |
| Question | `<<<RLX:QUESTION>>>` | Plan | Question for user |
| PlanDraft | `<<<RLX:PLAN_DRAFT>>>` | Plan | Draft for review |
| PlanReady | `<<<RLX:PLAN_READY>>>` | Plan | Plan written to disk |

## Key Interfaces (Protocols)

```python
class Executor(Protocol):
    def run(self, prompt: str, *, timeout: float | None = None) -> Result: ...

class Logger(Protocol):
    def print(self, format: str, *args: Any) -> None: ...
    def print_section(self, section: Section) -> None: ...
    def path(self) -> str: ...

class InputCollector(Protocol):
    def ask_question(self, question: str, options: list[str]) -> str: ...
    def ask_draft_review(self, question: str, plan_content: str) -> tuple[str, str]: ...

class GitChecker(Protocol):
    def head_hash(self) -> str: ...
    def diff_fingerprint(self) -> str: ...
```

## Reference Documentation

Detailed specifications for every module: `docs/reference/` (11 documents).
Read them before implementing — they contain exact types, algorithms, and behavior.
