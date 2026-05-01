# rlx

Python CLI for autonomous task execution via Claude Code. v0.1 implements plan creation only (`rlx --plan <file>`). The `--impl` flag stores intent for auto-implementation after plan creation (not yet implemented).

## Package structure

```
src/rlx/
  cli.py            - Typer entrypoint, mode dispatch, --impl flag, SIGINT handling
  config.py         - Config/ColorConfig dataclasses, TOML loading, parse_duration()
  status.py         - Phase/Signal constants, Section dataclass, PhaseHolder
  input.py          - TerminalCollector: interactive Q&A with numbered picker
  git.py            - Git helpers: is_git_repo, get_default_branch, head_hash
  executor/
    claude_executor.py - ClaudeExecutor: subprocess + JSON stream parsing, idle timeout, activity callbacks
    process_group.py   - ProcessGroupCleanup: SIGTERM/SIGKILL process group management
  processor/
    signals.py      - Signal payload parsing (QUESTION, PLAN_READY)
    prompts.py      - Prompt loading with local override fallback, variable substitution
    runner.py       - Runner: orchestrates plan creation loop via Protocol dependencies
  progress/
    colors.py       - Rich Style mapping from ColorConfig
    flock.py        - File locking via fcntl.flock
    logger.py       - Dual file+stdout logger with timestamps and signal highlighting
  defaults/
    prompts/        - Embedded prompt templates (make_plan.txt)
```

## Key commands

```bash
pytest tests/ -v                # run tests
ruff check src/ tests/          # lint
mypy src/                       # strict type check
rlx --version                   # verify CLI
make check                      # all of the above
```

## Coding conventions

- Python 3.14+, strict mypy
- Protocol-based interfaces for all Runner dependencies (Executor, Logger, InputCollector, GitChecker)
- Signal format: `<<<RLX:SIGNAL_NAME>>>` (e.g. `<<<RLX:PLAN_READY>>>`, `<<<RLX:QUESTION>>>`)

## Testing patterns

- Mock `CommandRunner` protocol for executor tests (avoid real Claude subprocess)
- Mock stdin/stdout for input/terminal tests
- Use `tmp_path` fixtures for file-based tests (config TOML, logger output, git repos)
- Never launch real Claude or require real git repos except via tmp_path
