# cadence

Python CLI for autonomous task execution via Claude Code. Supports `cadence --plan <file>` (plan creation), `cadence --task <file>` (full pipeline: branch creation → iterative task execution → review_first → review_loop → finalize), and `cadence --review` (review-only of the current branch: review_first → review_loop → finalize, no plan, no branch creation). The `--impl` flag chains `run_task_mode` on the derived plan path immediately after a successful `cadence --plan`, so `cadence --plan <file> --impl` runs the full pipeline in one command. `--review` is incompatible with `--impl`.

## Package structure

```
src/cadence/
  cli.py            - Typer entrypoint, mode dispatch, --plan/--task/--impl/--base/--config flags, SIGINT/SIGQUIT handling
  config.py         - Config/ColorConfig dataclasses, YAML loading via PyYAML, parse_duration(), YAML model overrides (load_yaml_config/apply_yaml_overrides/find_yaml_config); `tasks_root` (default `cdc-tasks`) is configurable in `.cadence/config.yaml`
  status.py         - Phase/Signal constants, Section dataclass, PhaseHolder
  input.py          - TerminalCollector: interactive Q&A with numbered picker, ask_yes_no()
  executor/
    claude_executor.py - ClaudeExecutor: subprocess + JSON stream parsing, idle timeout, activity callbacks
    process_group.py   - ProcessGroupCleanup: SIGTERM/SIGKILL process group management
    events.py          - Typed Claude stream event dataclasses (AssistantEvent, ContentBlockDeltaEvent, ResultEvent) + parse_event()
  git/
    __init__.py     - Re-exports: GitChecker, is_git_repo, get_default_branch, head_hash, Service, DiffStats
    backend.py      - ExternalBackend: git subprocess wrapper; DiffStats dataclass
    service.py      - Service: high-level git ops (branch creation for plan (no plan commit), commit trailer, rename plan in-place with -completed suffix)
  plan/
    __init__.py     - Re-exports: Plan, Task, Checkbox, TaskStatus, parse_plan, Selector, extract_branch_name
    parse.py        - Plan/Task/Checkbox dataclasses, markdown parsing, file_has_uncompleted_checkbox
    plan.py         - Selector (numbered picker + find_recent), extract_branch_name
  processor/
    signals.py      - Signal payload parsing (QUESTION, PLAN_READY, ALL_TASKS_DONE, TASK_FAILED, REVIEW_DONE) + is_* helpers
    prompts.py      - Prompt loading with local override fallback; build_plan_prompt, build_task_prompt, build_review_first_prompt, build_review_second_prompt, build_finalize_prompt; expand_agent_references / format_agent_expansion / replace_prompt_variables
    agents.py       - Agent loader (local .cadence/agents/<name>.txt → embedded cadence.defaults.agents); AgentDef, frontmatter parser, model normalization
    runner.py       - Runner: orchestrates plan creation, task execution, review (run_claude_review + run_claude_review_loop), and finalize phases via Protocol dependencies; supports an optional second review_executor; break/pause + session timeout; Mode.REVIEW dispatch
  progress/
    colors.py       - Rich Style mapping from ColorConfig
    flock.py        - File locking via fcntl.flock
    logger.py       - Dual file+stdout logger with timestamps and signal highlighting; resolves the progress path per mode (`progress-plan.txt`/`progress-task.txt` next to the plan file for plan/full; `<tasks_root>/<branch-or-head-hash>/progress-review.txt` for review)
  defaults/
    prompts/        - Embedded prompt templates (make_plan.txt, task.txt, review_first.txt, review_second.txt, finalize.txt)
    agents/         - Embedded agent bodies (quality.txt, implementation.txt, testing.txt, simplification.txt) referenced from review prompts via {{agent:<name>}} markers
```

## Key commands

Run tools directly from the project venv (`source venv/bin/activate`). Do NOT use `pdm run`.

```bash
pytest tests/ -v                # run tests
ruff check src/ tests/          # lint
ruff format src/ tests/         # format
mypy src/                       # strict type check
cadence --version               # verify CLI
make check                      # all of the above
```

## Coding conventions

- Python 3.14+, strict mypy
- Protocol-based interfaces for all Runner dependencies (Executor, Logger, InputCollector, GitChecker)
- Signal format: `<<<CADENCE:SIGNAL_NAME>>>` (e.g. `<<<CADENCE:PLAN_READY>>>`, `<<<CADENCE:QUESTION>>>`)

## Testing patterns

- Mock `CommandRunner` protocol for executor tests (avoid real Claude subprocess)
- Mock stdin/stdout for input/terminal tests
- Use `tmp_path` fixtures for file-based tests (config YAML, logger output, git repos)
- Never launch real Claude or require real git repos except via tmp_path

## Deeper reference

Module-level details live in `docs/`: `config.md`, `processor.md`, `executor.md`, `git-and-plans.md`, `progress-and-input.md`. Read on demand.

## Commit messages

Format: `<branch-name>. Added: <what>. Changed: <what>. Deleted: <what>.` Include only the sections that apply. English, single line.

Each section is **one short clause** in plain language describing the user-visible outcome — what someone reading `git log --oneline` cares about. Implementation details (method/test/file names, renames, formatter passes, doc syncs) belong in the diff, not the subject line. If a section needs more than one clause, the commit is probably too big. When squashing, write a fresh summary — do not concatenate the sub-commit messages.

Good: `0014-no-plan-commit-on-start. Changed: cadence no longer auto-commits the plan file when starting a task. Deleted: now-unused commit_plan_file / file_has_changes helpers.`

Bad (verbose, name-listing, sub-commit concat): `0014-... Changed: _prepare_plan_branch returns only branch name (drops needs_commit), create_branch_for_plan no longer auto-commits, ruff format applied, test_creates_branch_and_commits renamed to test_creates_branch_no_commit, ...`

Author as the user — no `Co-Authored-By` trailer.
