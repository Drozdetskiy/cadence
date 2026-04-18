# rlx v0.1 -- ModePlan (Interactive Plan Creation via Claude)

## Overview

Implement rlx v0.1: a Python CLI tool for interactive plan creation via Claude Code. This version implements only `rlx --plan <file>` -- reads a file with a task description and creates an implementation plan through an interactive Q&A dialogue with Claude. The plan file is written by Claude next to the source file as `<file>-plan.md`.

## Context

- Files involved: Greenfield -- all files created under `src/rlx/`, `tests/`, plus `pyproject.toml`
- Related patterns: Full specification in `docs/reference/` (10 documents), signal format `<<<RLX:...>>>`
- Dependencies: runtime (typer, rich), dev (pytest, ruff, mypy, pytest-cov, pytest-mock)
- Package manager: pdm
- Python: 3.14+

## Development Approach

- **Testing approach**: Regular (code first, then tests)
- Complete each task fully before moving to the next
- Follow the specification in `docs/reference/` strictly for types, interfaces, and behavior
- Use Protocol classes for all Runner dependencies (Executor, Logger, InputCollector, GitChecker)
- Mock subprocess/stdin/stdout in tests -- never launch real Claude or require real git repos except via tmp_path
- **CRITICAL: every task MUST include new/updated tests**
- **CRITICAL: all tests must pass before starting next task**

## Implementation Steps

### Task 1: Project scaffold + leaf modules (status.py, config.py)

**Files:**
- Create: `pyproject.toml`
- Create: `src/rlx/__init__.py`
- Create: `src/rlx/status.py`
- Create: `src/rlx/config.py`
- Create: `tests/__init__.py`
- Create: `tests/test_status.py`
- Create: `tests/test_config.py`

- [x] Create `pyproject.toml`: pdm build-backend, name="rlx", dynamic version from `__init__.py`, requires-python=">=3.14", dependencies=[typer, rich], dev dependencies=[pytest, ruff, mypy, pytest-cov, pytest-mock], `[project.scripts] rlx = "rlx.cli:main"`, ruff config, mypy strict mode, pytest testpaths
- [x] Create `src/rlx/__init__.py` with `__version__` via `importlib.metadata.version("rlx")`
- [x] Implement `src/rlx/status.py`: Phase type (string constants: PhaseTask, PhaseReview, PhasePlan, PhaseFinalize), Signal string constants (Completed=`<<<RLX:ALL_TASKS_DONE>>>`, Failed=`<<<RLX:TASK_FAILED>>>`, ReviewDone=`<<<RLX:REVIEW_DONE>>>`, Question=`<<<RLX:QUESTION>>>`, PlanReady=`<<<RLX:PLAN_READY>>>`, PlanDraft=`<<<RLX:PLAN_DRAFT>>>`, End=`<<<RLX:END>>>`), Section dataclass with label field, helper functions (new_task_iteration_section, new_claude_review_section, new_plan_iteration_section, new_generic_section), PhaseHolder class (thread-safe via threading.Lock: get/set/on_change callback)
- [x] Implement `src/rlx/config.py`: ColorConfig dataclass (hex defaults: task="#2e8b57", review="#1a9e9e", warn="#d4930d", error="#cc0000", signal="#d25252", timestamp="#707070", info="#808080"), Config dataclass (all fields from spec with defaults -- claude_command, claude_args, claude_model, review_model, iteration_delay_ms, task_retry_count, max_iterations, session_timeout, idle_timeout, wait_on_limit, finalize_enabled, plans_dir, default_branch, vcs_command, commit_trailer, claude_error_patterns, claude_limit_patterns, colors), parse_duration() for "30m"/"1h"/"90s"/"1h30m" strings to float seconds, load_config(config_dir) with TOML merge via tomllib, detect_local_dir()
- [x] Run `pdm install --dev` to bootstrap the project
- [x] Write tests: Phase/Signal constants, Section helpers, PhaseHolder thread-safety, Config defaults, parse_duration (valid formats, compound "1h30m", zero, invalid), TOML merge via tmp_path fixtures, ColorConfig defaults
- [x] Run `pdm run pytest tests/ -v` -- must pass
- [x] Run `pdm run ruff check src/ tests/` -- no errors
- [x] Run `pdm run mypy src/` -- passes

### Task 2: I/O layer (progress/, input.py)

**Files:**
- Create: `src/rlx/progress/__init__.py`
- Create: `src/rlx/progress/colors.py`
- Create: `src/rlx/progress/flock.py`
- Create: `src/rlx/progress/logger.py`
- Create: `src/rlx/input.py`
- Create: `tests/test_progress.py`
- Create: `tests/test_input.py`

- [x] Implement `progress/colors.py`: Colors class taking ColorConfig, hex-to-rich-Style conversion, for_phase(Phase) mapping (PhasePlan/PhaseTask/PhaseFinalize -> task, PhaseReview -> review), accessor methods (timestamp, warn, error, signal, info)
- [x] Implement `progress/flock.py`: lock_file(), unlock_file(), try_lock_file() via fcntl.flock on Unix; no-op stubs on Windows (sys.platform check)
- [x] Implement `progress/logger.py`: Logger.Config dataclass (plan_file, plan_description, mode, branch, no_color), Logger class with file + stdout dual output, timestamp format "[YY-MM-DD HH:MM:SS]", all methods from spec (print, print_raw, print_section, print_aligned with word wrap and signal highlighting, error, warn, log_question/log_answer/log_draft_review, elapsed, close with footer, path), _progress_filename generation (mode-dependent naming, _sanitize_plan_name), header/restart/footer file format, completion detection
- [x] Implement `src/rlx/input.py`: ACTION_ACCEPT/REVISE/REJECT constants, TerminalCollector class with ask_question (numbered picker with "Other" option, collision filtering), ask_draft_review (rich markdown rendering, 4-option menu, $EDITOR support via subprocess with VISUAL/EDITOR/vi fallback, difflib.unified_diff for interactive review), ask_yes_no ([y/N] format), read_line_with_context
- [x] Write tests: Colors hex parsing and phase mapping, flock operations, Logger creation/print/section/close with tmp_path, filename generation for each mode, TerminalCollector ask_question/ask_draft_review/ask_yes_no with mock stdin/stdout
- [x] Run `pdm run pytest tests/ -v` -- must pass
- [x] Run `pdm run ruff check src/ tests/` -- no errors

### Task 3: Execution layer (executor/, git.py)

**Files:**
- Create: `src/rlx/executor/__init__.py`
- Create: `src/rlx/executor/process_group.py`
- Create: `src/rlx/executor/claude_executor.py`
- Create: `src/rlx/git.py`
- Create: `tests/test_executor.py`

- [x] Implement `executor/process_group.py`: ProcessGroupCleanup class for Unix (kill_process_group: SIGTERM -> 100ms sleep -> SIGKILL via os.killpg, ProcessLookupError early-return; wait: process.wait + orphan cleanup)
- [x] Implement `executor/claude_executor.py`: Result dataclass (output, recent_text via deque(maxlen=10), signal, error, idle_timed_out), PatternMatchError/LimitPatternError exceptions, CommandRunner protocol, detect_signal() (substring search, returns first match or ""), match_pattern() (case-insensitive substring, skip empty/whitespace), ClaudeExecutor class: command building (args via shlex.split or defaults, model flag, --print), filter_env (remove ANTHROPIC_API_KEY and CLAUDECODE), run(prompt) with Popen(stdin=PIPE, stdout=PIPE, stderr=STDOUT, text=True, start_new_session=True), parse_stream (JSON line-by-line, extract_text per event type: assistant/content_block_delta/message_stop/result), idle timeout via threading.Timer with reset on each line, limit pattern check before error pattern check, exit code handling with cancellation bypass
- [x] Implement `src/rlx/git.py`: minimal v0.1 -- is_git_repo() check, get_default_branch() (symbolic-ref -> fallback list [main, master, trunk, develop] -> "master"), head_hash() (git rev-parse HEAD); all via subprocess.run; also a simple GitChecker class implementing the protocol from runner
- [x] Write tests: detect_signal (each signal, no signal, multiple), match_pattern (case-insensitive, empty, whitespace), ClaudeExecutor with mock CommandRunner (JSON stream parsing, signal detection, error/limit patterns, idle timeout, exit codes), git functions via tmp_path with real git init
- [x] Run `pdm run pytest tests/ -v` -- must pass
- [x] Run `pdm run ruff check src/ tests/` -- no errors

### Task 4: Processor module (signals, prompts, runner) + embedded defaults

**Files:**
- Create: `src/rlx/defaults/__init__.py`
- Create: `src/rlx/defaults/prompts/__init__.py`
- Create: `src/rlx/defaults/prompts/make_plan.txt`
- Create: `src/rlx/processor/__init__.py`
- Create: `src/rlx/processor/signals.py`
- Create: `src/rlx/processor/prompts.py`
- Create: `src/rlx/processor/runner.py`
- Create: `tests/test_signals.py`
- Create: `tests/test_processor.py`

- [x] Create `src/rlx/defaults/prompts/make_plan.txt` with the full plan creation prompt from `docs/reference/08-prompts.md` (using `<<<RLX:...>>>` signal markers)
- [x] Implement `processor/signals.py`: QuestionPayload dataclass, parse_question_payload(output) with regex between QUESTION/END markers + json.loads + validation, parse_plan_draft_payload(output) with regex between PLAN_DRAFT/END markers, is_plan_ready(signal), is_review_done(signal)
- [x] Implement `processor/prompts.py`: normalize_crlf(), strip_comments(), strip_leading_comments() (block of 2+ consecutive # lines at start), load_prompt(name) with per-file fallback (local .rlx/prompts/ -> embedded via importlib.resources), replace_base_variables() (PLAN_FILE, PROGRESS_FILE, GOAL, DEFAULT_BRANCH, PLANS_DIR), append_commit_trailer_instruction(), build_plan_prompt() (load make_plan.txt, substitute PLAN_DESCRIPTION, apply base variables + trailer)
- [x] Implement `processor/runner.py`: Runner.Config dataclass, Executor/Logger/InputCollector/GitChecker Protocol classes, Executors dataclass, UserAbortedError/UserRejectedPlanError exceptions, Runner class with constructor (from config + from_executors for testing), setter methods (set_input_collector, set_git_checker), run() dispatch by mode, run_plan_creation() (loop 1..max_plan_iterations, build prompt, append last_revision_feedback if any, execute via claude, handle signals: PLAN_READY->success, QUESTION->parse+ask+log, PLAN_DRAFT->ask_draft_review+log, TASK_FAILED->error; clear revision feedback on non-timeout; iteration_delay between loops), handle_plan_draft(), handle_plan_question(), handle_pattern_match_error()
- [x] Write tests for signals: parse_question_payload (valid/malformed/missing/no-signal), parse_plan_draft_payload (valid/empty/no-markers), is_plan_ready, is_review_done
- [x] Write tests for processor: strip/normalize functions, load_prompt with fallback (tmp_path), build_plan_prompt variable substitution, Runner.run_plan_creation with mock Executor/Logger/InputCollector testing flows: QUESTION->answer->PLAN_DRAFT->accept->PLAN_READY, revision feedback, reject, max iterations, error handling
- [x] Run `pdm run pytest tests/ -v` -- must pass
- [x] Run `pdm run ruff check src/ tests/` -- no errors

### Task 5: CLI entrypoint and integration

**Files:**
- Create: `src/rlx/cli.py`
- Create: `tests/test_cli.py`

- [x] Implement `src/rlx/cli.py`: typer app, main() command with --plan (Path, default None), --task (Path, default None, stub for v0.1), --review (bool, default False, stub), --version (bool, default False); determine_mode() (plan > task > review, mutual exclusivity validation); version display via importlib.metadata; check_claude_dep() via shutil.which; run_plan_mode(plan_file): read file content, load_config, validate (file exists, claude in PATH, is git repo), create Logger with progress config, create ClaudeExecutor from config, create Runner from_executors, set TerminalCollector as input_collector, set GitChecker, runner.run(), after PLAN_READY ask "Continue with implementation?" via ask_yes_no (v0.1: always just exit regardless); SIGINT handler via signal.signal (first: graceful shutdown via threading.Event, repeat within 5s: sys.exit(1)); to_rel_path() helper
- [x] Write tests: --version output, determine_mode logic, mutual exclusivity validation, run_plan_mode integration with mocked executor verifying full wiring (config -> executor -> runner -> result), SIGINT handler setup
- [x] Run `pdm run pytest tests/ -v` -- must pass
- [x] Run `pdm run ruff check src/ tests/` -- no errors
- [x] Run `pdm run mypy src/` -- full strict type check passes

### Task 6: Verify acceptance criteria

- [x] Run `pdm run pytest tests/ -v` -- full test suite passes
- [x] Run `pdm run ruff check src/ tests/` -- no linter errors
- [x] Run `pdm run mypy src/` -- strict type checking passes
- [x] Verify `pdm run rlx --version` outputs the version string correctly
- [x] Verify test coverage meets 80%+ via `pdm run pytest tests/ --cov=src/rlx --cov-report=term-missing`

### Task 7: Update documentation

- [ ] Update `README.md` with: project description, installation (pdm install / pip install -e .), usage (`rlx --plan <file>`, `rlx --version`), development setup (pdm install --dev), testing commands (make test, make lint, make typecheck, make check)
- [ ] Create `CLAUDE.md` with: project purpose (Python CLI for autonomous task execution via Claude Code, v0.1 = plan creation only), package structure (src/rlx/ layout), key commands (pdm run pytest, pdm run ruff check, pdm run mypy), coding conventions (Python 3.14+, strict mypy, Protocol-based interfaces), testing patterns (mock CommandRunner for executor, mock stdin/stdout for input, tmp_path for file-based tests), signal format (`<<<RLX:...>>>`), architecture notes (Runner orchestrates via protocols, ClaudeExecutor handles subprocess + streaming)
- [ ] Move this plan to `docs/plans/completed/`
