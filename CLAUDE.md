# rlx

Python CLI for autonomous task execution via Claude Code. Supports `rlx --plan <file>` (plan creation), `rlx --task <file>` (full pipeline: branch creation → iterative task execution → review_first → review_loop → finalize), and `rlx --review` (review-only of the current branch: review_first → review_loop → finalize, no plan, no branch creation). The `--impl` flag stores intent for auto-implementation after plan creation (not yet implemented). `--review` is incompatible with `--impl`.

## Package structure

```
src/rlx/
  cli.py            - Typer entrypoint, mode dispatch, --plan/--task/--impl flags, SIGINT/SIGQUIT handling
  config.py         - Config/ColorConfig dataclasses, TOML loading, parse_duration()
  status.py         - Phase/Signal constants, Section dataclass, PhaseHolder
  input.py          - TerminalCollector: interactive Q&A with numbered picker, ask_yes_no()
  executor/
    claude_executor.py - ClaudeExecutor: subprocess + JSON stream parsing, idle timeout, activity callbacks
    process_group.py   - ProcessGroupCleanup: SIGTERM/SIGKILL process group management
  git/
    __init__.py     - Re-exports: GitChecker, is_git_repo, get_default_branch, head_hash, Service, DiffStats
    backend.py      - ExternalBackend: git subprocess wrapper; DiffStats dataclass
    service.py      - Service: high-level git ops (branch creation for plan, commit trailer, move plan to completed/)
  plan/
    __init__.py     - Re-exports: Plan, Task, Checkbox, TaskStatus, parse_plan, Selector, extract_branch_name
    parse.py        - Plan/Task/Checkbox dataclasses, markdown parsing, file_has_uncompleted_checkbox
    plan.py         - Selector (numbered picker + find_recent), extract_branch_name
  processor/
    signals.py      - Signal payload parsing (QUESTION, PLAN_READY, ALL_TASKS_DONE, TASK_FAILED, REVIEW_DONE) + is_* helpers
    prompts.py      - Prompt loading with local override fallback; build_plan_prompt, build_task_prompt, build_review_first_prompt, build_review_second_prompt, build_finalize_prompt; expand_agent_references / format_agent_expansion / replace_prompt_variables
    agents.py       - Agent loader (local .rlx/agents/<name>.txt → embedded rlx.defaults.agents); AgentDef, frontmatter parser, model normalization
    runner.py       - Runner: orchestrates plan creation, task execution, review (run_claude_review + run_claude_review_loop), and finalize phases via Protocol dependencies; supports an optional second review_executor; break/pause + session timeout; Mode.REVIEW dispatch
  progress/
    colors.py       - Rich Style mapping from ColorConfig
    flock.py        - File locking via fcntl.flock
    logger.py       - Dual file+stdout logger with timestamps and signal highlighting
  defaults/
    prompts/        - Embedded prompt templates (make_plan.txt, task.txt, review_first.txt, review_second.txt, finalize.txt)
    agents/         - Embedded agent bodies (quality.txt, implementation.txt, testing.txt, simplification.txt, documentation.txt) referenced from review prompts via {{agent:<name>}} markers
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
