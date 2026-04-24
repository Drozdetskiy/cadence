# rlx v0.2 -- Task Execution Mode (`--task`)

## Overview

Implement `rlx --task <file>` for autonomous task execution. Reads a markdown plan, creates a git branch, iteratively executes tasks through Claude Code, tracks progress via checkboxes, and reports diff stats on completion.

## Context

- Files involved: `src/rlx/cli.py`, `src/rlx/git.py` (restructure to `src/rlx/git/`), `src/rlx/processor/runner.py`, `src/rlx/processor/prompts.py`, `src/rlx/input.py`, `src/rlx/status.py`
- New modules: `src/rlx/plan/` (parse + select), `src/rlx/git/backend.py`, `src/rlx/git/service.py`, `src/rlx/defaults/prompts/task.txt`
- Related patterns: Protocol-based interfaces (Runner already uses Executor, Logger, GitChecker protocols), embedded resources via `importlib.resources`, signal-based communication (`<<<RLX:...>>>`)
- Reference specs: `docs/reference/01-architecture.md` through `docs/reference/10-features.md` -- exhaustive spec for all modules
- Dependencies: no new external dependencies (stdlib threading, pathlib, subprocess, re, hashlib)

## Development Approach

- **Testing approach**: Regular (code first, then tests)
- Complete each task fully before moving to the next
- Follow existing v0.1 patterns: Protocol interfaces, dataclasses, strict mypy
- Reference specs in `docs/reference/` are authoritative -- code must match spec
- **CRITICAL: every task MUST include new/updated tests**
- **CRITICAL: all tests must pass before starting next task**

## Implementation Steps

### Task 1: Plan module (`src/rlx/plan/`)

**Files:**
- Create: `src/rlx/plan/__init__.py`
- Create: `src/rlx/plan/parse.py`
- Create: `src/rlx/plan/plan.py`
- Create: `tests/test_plan.py`

Spec reference: `docs/reference/06-git-and-plans.md` section "Модуль plan"

- [x] Create `src/rlx/plan/__init__.py` with re-exports
- [x] Create `src/rlx/plan/parse.py` with types and parsing:
  - `TaskStatus` enum (PENDING, ACTIVE, DONE, FAILED)
  - `Checkbox` dataclass (text, checked) with `is_actionable()` method (uses `format_in_text` regex to detect format-description checkboxes)
  - `Task` dataclass (number, title, status, checkboxes) with `has_uncompleted_actionable_work()` method
  - `Plan` dataclass (title, tasks)
  - `determine_task_status(checkboxes)` function
  - `parse_plan(content: str) -> Plan` -- parse markdown plan with `### Task N:` / `### Iteration N:` headers, `- [ ]` / `- [x]` checkboxes, `##` / `#` section boundaries close current task
  - `parse_plan_file(path: str) -> Plan` -- wrapper that reads file then calls parse_plan
  - `file_has_uncompleted_checkbox(path: str) -> bool` -- scan file for `- [ ]` without task headers (malformed plan fallback), ignoring format-description checkboxes
- [x] Create `src/rlx/plan/plan.py` with selection and branch name extraction:
  - `NoPlansFoundError` exception
  - `extract_branch_name(plan_file: str) -> str` -- strip .md extension, strip date prefix via regex `^[\d-]+`, strip leading dashes, fallback to original stem if empty
  - `Selector` class with `__init__(self, plans_dir: str, colors: Colors)`:
    - `select(plan_file: str, optional: bool) -> str` -- validate existence if provided, return empty if optional and not provided, numbered selection otherwise, always return absolute path
    - `_select_with_numbers() -> str` -- glob `plans_dir/*.md`, auto-select if single file, numbered picker if multiple, `NoPlansFoundError` if none
    - `find_recent(start_time: datetime) -> str` -- find most recently modified plan file after start_time
- [x] Write tests in `tests/test_plan.py` covering: parse_plan with various markdown structures, TaskStatus determination, checkbox actionability, extract_branch_name with date prefixes, file_has_uncompleted_checkbox, Selector with tmp_path fixtures
- [x] Run `make check` -- must pass before Task 2

### Task 2: Git module expansion (`src/rlx/git/`)

**Files:**
- Rename: `src/rlx/git.py` -> restructure into `src/rlx/git/` package
- Create: `src/rlx/git/__init__.py`
- Create: `src/rlx/git/backend.py`
- Create: `src/rlx/git/service.py`
- Modify: `src/rlx/cli.py` (update imports from `rlx.git`)
- Modify: `tests/test_git.py` (update imports)
- Create: `tests/test_git_service.py`

Spec reference: `docs/reference/06-git-and-plans.md` sections "Backend интерфейс", "ExternalBackend", "Service class"

- [x] Create `src/rlx/git/backend.py` with `DiffStats` dataclass and `ExternalBackend` class:
  - `DiffStats(files=0, additions=0, deletions=0)`
  - `ExternalBackend.__init__(path)` -- resolve path, validate via `git rev-parse --show-toplevel`, resolve symlinks
  - `_run(*args) -> str` -- subprocess.run with cwd, capture_output, rstrip trailing whitespace
  - State methods: `root()`, `head_hash()`, `has_commits()` (LC_ALL=C, exit 128 + "ambiguous argument" = empty repo), `current_branch()` (symbolic-ref, exit 128 + "not a symbolic ref" = detached HEAD), `get_default_branch()` (symbolic-ref origin/HEAD -> candidates -> "master")
  - Branch ops: `branch_exists(name)` (show-ref --verify), `create_branch(name)` (checkout -b), `checkout_branch(name)`
  - Diff ops: `diff_fingerprint()` (SHA256 of `git diff HEAD` + untracked files hash-objects), `is_dirty()` (status --porcelain, ignore `??`), `file_has_changes(path)` (status --porcelain -uall), `has_changes_other_than(path)` (parse porcelain, case-insensitive exclude), `diff_stats(base_branch)` (resolve_ref + numstat parsing)
  - File ops: `add(path)`, `move_file(src, dst)`, `commit(msg)`, `commit_files(msg, *paths)`, `create_initial_commit(msg)` (add -A + commit)
  - Helpers: `_to_relative(path)`, `_resolve_ref(branch_name)` (try refs/heads, refs/remotes/origin, rev-parse --verify), `_ref_exists(ref)`, `_extract_path_from_porcelain(line)` (handle renames)
- [x] Create `src/rlx/git/service.py` with `Service` class:
  - `__init__(path, log)` -- create ExternalBackend, store logger
  - `set_commit_trailer(trailer)`, `_append_trailer(msg)` -- add `"\n\n" + trailer` to commit messages
  - Delegating methods: `root()`, `head_hash()`, `diff_fingerprint()`, `current_branch()`, `get_default_branch()`, `has_commits()`, `diff_stats(base_branch)`, `file_has_changes(path)`
  - `is_default_branch(default_branch)` -- compare current branch with default
  - `create_branch(name)` -- delegate to backend
  - `create_branch_for_plan(plan_file, default_branch)` -- resolve filesystem case, prepare branch, check if already on feature branch, checkout or create, auto-commit plan if only dirty file
  - `_prepare_plan_branch(plan_file, default_branch) -> tuple[str, bool]` -- check current branch, extract branch name via `plan.extract_branch_name`, check dirty files
  - `commit_plan_file(plan_file)` -- git add + commit "add plan: <branch>"
  - `move_plan_to_completed(plan_file)` -- create completed/ dir, git mv (fallback os.rename + git add), commit
  - `ensure_has_commits(prompt_fn)` -- check + prompt + initial commit
  - `_resolve_filesystem_case(path)` -- case-insensitive match via os.listdir
- [x] Create `src/rlx/git/__init__.py` that re-exports: `GitChecker`, `is_git_repo`, `get_default_branch`, `head_hash` (preserve backward compat), plus new exports `Service`, `DiffStats`
  - Move existing `is_git_repo`, `get_default_branch`, `head_hash` functions and `GitChecker` class from old `git.py` into `__init__.py` (or into backend.py and re-export)
- [x] Delete old `src/rlx/git.py`
- [x] Update imports in `src/rlx/cli.py` and any other files that import from `rlx.git` -- existing imports should continue to work via `__init__.py` re-exports
- [x] Update `tests/test_git.py` imports if needed
- [x] Write tests in `tests/test_git_service.py` covering: ExternalBackend operations with tmp_path git repos, Service.create_branch_for_plan flow, Service.move_plan_to_completed, DiffStats parsing, commit trailer appending, _resolve_filesystem_case, _extract_path_from_porcelain
- [x] Run `make check` -- must pass before Task 3

### Task 3: Task prompt and prompts extension

**Files:**
- Create: `src/rlx/defaults/prompts/task.txt`
- Modify: `src/rlx/processor/prompts.py`
- Create or modify: `tests/test_prompts.py`

Spec reference: `docs/reference/08-prompts.md` section "task.txt"

- [x] Create `src/rlx/defaults/prompts/task.txt` with the full task prompt from the spec (the exact text is in `docs/reference/08-prompts.md`). The prompt uses `{{PLAN_FILE}}`, `{{PROGRESS_FILE}}`, `{{GOAL}}`, `{{DEFAULT_BRANCH}}` variables and `<<<RLX:ALL_TASKS_DONE>>>` / `<<<RLX:TASK_FAILED>>>` signals
- [x] Add `build_task_prompt()` to `src/rlx/processor/prompts.py`:
  - Load "task" prompt via `load_prompt("task", local_dir)`
  - `replace_base_variables()` with goal=`"implementation of plan at {plan_file}"`
  - `append_commit_trailer_instruction()`
  - In v0.2 this is equivalent to `replace_prompt_variables()` without agent expansion (agents are v0.3)
- [x] Write tests covering: build_task_prompt produces expected output with variables substituted, commit trailer appended when configured, goal format matches spec
- [x] Run `make check` -- must pass before Task 4

### Task 4: Runner extension

**Files:**
- Modify: `src/rlx/processor/runner.py`
- Modify: `tests/test_processor.py`

Spec reference: `docs/reference/04-processor.md` sections "run_task_phase", "run_full", "run_tasks_only", "Session timeout", "Break/pause", "sleep_with_cancel", "has_uncompleted_tasks", "next_plan_task_position"

- [x] Add `run_tasks_only()` to Runner -- sets PhaseTask, calls `run_task_phase()`, requires plan_file
- [x] Add `run_full()` to Runner -- in v0.2 this only calls `run_task_phase()` (review/finalize phases are v0.3). Sets PhaseTask, validates plan_file not empty
- [x] Update `run()` dispatch to handle `"full"` and `"tasks-only"` modes in addition to `"plan"`
- [x] Implement `run_task_phase()`:
  - Build task prompt once via `build_task_prompt()`
  - Loop 1..max_iterations:
    - Determine task number via `next_plan_task_position()` (fallback to loop counter)
    - Print section via `new_task_iteration_section(task_num)`
    - Call `run_with_limit_retry(claude.run, prompt)`
    - Check `is_break()` -- if break: clear, call pause_handler, resume (i-=1, reset retry) or raise UserAbortedError
    - Handle errors (PatternMatchError -> return, other -> raise)
    - Handle COMPLETED signal: check `has_uncompleted_tasks()`, if uncompleted warn and continue, if all done return True
    - Handle FAILED signal: retry up to task_retry_count, then raise
    - Reset retry_count, sleep iteration_delay
  - After loop: warn max iterations, return False
- [x] Implement `has_uncompleted_tasks() -> bool`:
  - Resolve plan file path (check original, then completed/)
  - `parse_plan_file()` and iterate tasks checking `has_uncompleted_actionable_work()`
  - Fallback: `file_has_uncompleted_checkbox()` for malformed plans
- [x] Implement `next_plan_task_position() -> int`:
  - `parse_plan_file()`, find first task with uncompleted actionable work
  - Return 1-indexed position, or 0 if none found
- [x] Implement `run_with_session_timeout()`:
  - If session_timeout <= 0: run directly, check idle_timed_out
  - Else: wrap with threading.Timer, on timeout kill process
  - Set `last_session_timed_out` flag when timeout fires
  - Clear result.error and result.signal on session timeout (can't trust partial session)
- [x] Implement `sleep_with_cancel(duration)` using threading.Event.wait(timeout)
- [x] Add break/pause fields to Runner: `break_event: threading.Event | None`, `pause_handler: Callable[[], bool] | None`
- [x] Add setter methods: `set_break_event(event)`, `set_pause_handler(fn)`
- [x] Add helpers: `_is_break() -> bool` (break_event.is_set() and not cancelled), `_clear_break()`
- [x] Add `last_session_timed_out: bool` field, `session_timeout: float` from config
- [x] Add `resolve_plan_file_path() -> str` -- check original path, then completed/ subdir
- [x] Update RunnerConfig if needed (session_timeout field)
- [x] Update Logger protocol to include `print_aligned` method (used by task phase output)
- [x] Write tests covering: run_task_phase with mock executor (COMPLETED signal, FAILED with retry, max iterations), has_uncompleted_tasks with various plan states, next_plan_task_position, break/pause flow, session timeout, sleep_with_cancel
- [x] Run `make check` -- must pass before Task 5

### Task 5: CLI wiring

**Files:**
- Modify: `src/rlx/cli.py`
- Modify: `src/rlx/input.py`
- Modify: `tests/test_cli.py`

Spec reference: `docs/reference/03-cli.md` sections "run_full_mode", "execute_plan", signal handling

- [x] Add `ask_yes_no(prompt) -> bool` to `src/rlx/input.py` -- prompt with `[y/N]`, "y"/"yes" -> True, everything else -> False, EOF -> False
- [x] Implement `run_task_mode(task_file: Path)` in `src/rlx/cli.py`:
  - Validate task_file exists
  - Load config, check deps (claude, git repo)
  - Create `git.Service(path=".", log=...)` and `set_commit_trailer()`
  - `ensure_has_commits()` with ask_yes_no callback
  - Resolve default branch
  - `git_svc.create_branch_for_plan(plan_file, default_branch)`
  - Create logger (mode="full", plan_file, branch)
  - Print startup info (version, mode, plan file, branch, progress path)
  - Create executors (ClaudeExecutor for task, separate one for review model if different)
  - Create Runner with RunnerConfig(mode="full", ...)
  - Set up break_event (threading.Event) and pause_handler
  - Set git_checker on runner
  - Call `runner.run()`
  - On success: `git_svc.diff_stats(default_branch)`, `git_svc.move_plan_to_completed(plan_file)`, `display_stats()`
  - Error handling: UserAbortedError -> log "aborted", KeyboardInterrupt -> log "interrupted", other -> raise
  - Close logger in finally block
- [x] Add SIGQUIT handler for break/pause:
  - `signal.signal(signal.SIGQUIT, sigquit_handler)` -- sets break_event
  - Pause handler function: prints "session interrupted. press Enter to continue, Ctrl+C to abort", waits for input, returns True (continue) or False (abort)
- [x] Add `display_stats(stats: DiffStats, elapsed: str, branch: str)` helper
- [x] Wire `--task` flag in main(): replace "not implemented" error with `run_task_mode(task)` call
- [x] Write tests covering: run_task_mode with mocked git.Service and Runner, ask_yes_no various inputs, determine_mode with --task flag, display_stats formatting
- [x] Run `make check` -- must pass before Task 6

### Task 6: Verify acceptance criteria

- [x] Run full test suite: `pytest tests/ -v`
- [x] Run linter: `ruff check src/ tests/`
- [x] Run type checker: `mypy src/`
- [x] Verify all new modules have test coverage

### Task 7: Update documentation

- [x] Update `CLAUDE.md` with new package structure (plan/, git/ package instead of git.py)
- [x] Move this plan to `tasks/0003-v02/completed/`
