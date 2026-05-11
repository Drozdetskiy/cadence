# Processor / Orchestration Layer

Reference document for the orchestration layer.

## Overview

The processor module is the core of cadence. It contains `Runner`, which drives the entire execution lifecycle: from running tasks through review. Runner does not know about the CLI, configuration files, or git operations directly — it works through interfaces.

Key modules:
- `processor/runner.py` — Runner class, all run-methods, iteration loops
- `processor/prompts.py` — prompt template system, variable substitution, agent expansion
- `processor/signals.py` — signal parsing (QUESTION, PLAN_DRAFT), helper functions

## Runner class and dependencies

```python
class Runner:
    cfg: Config                          # Runner configuration
    log: Logger                          # progress logging
    claude: Executor                     # executor for task phase
    review_claude: Executor              # executor for review phases (may use a different model)
    git: GitChecker                      # HEAD hash / diff fingerprint inspection
    input_collector: InputCollector      # user input (plan creation)
    phase_holder: PhaseHolder            # thread-safe current phase
    iteration_delay: float               # pause between iterations (default 2.0 sec)
    task_retry_count: int                # retry count on FAILED (default 1)
    wait_on_limit: float                 # wait time on rate limit (sec)
    break_event: threading.Event         # event for break signal (Ctrl+\)
    pause_handler: Callable[[],  bool]   # pause/resume callback
    last_session_timed_out: bool         # flag: last session ended via timeout
    task_phase_override: Callable | None # test seam
```

### Config dataclass

```python
@dataclass
class Config:
    plan_file: str              # path to the plan file
    plan_description: str       # description for plan creation mode
    progress_path: str          # path to the progress file
    mode: Mode                  # execution mode
    max_iterations: int         # max iterations of task phase
    debug: bool                 # debug output
    no_color: bool              # disable colors
    iteration_delay_ms: int     # delay between iterations (ms)
    task_retry_count: int       # retry on FAILED
    plan_model: str             # model for plan creation phase
    task_model: str             # model for task phase
    review_model: str           # model for review phases
    default_branch: str         # default branch (from git)
    app_config: AppConfig       # full application config
```

### Protocols (interfaces)

Runner defines 4 protocols:

**Executor** — runs the CLI and returns the result:
```python
class Executor(Protocol):
    def run(self, prompt: str) -> Result: ...
```

**Logger** — progress logging with support for structured sections and Q&A:
```python
class Logger(Protocol):
    def print(self, format: str, *args) -> None: ...        # formatted line with timestamp
    def print_section(self, section: Section) -> None: ...  # section header
    def print_aligned(self, text: str) -> None: ...         # aligned output (for streaming)
    def log_question(self, question: str, options: list[str]) -> None: ...  # Q&A for plan creation
    def log_answer(self, answer: str) -> None: ...
    def log_draft_review(self, action: str, feedback: str) -> None: ...
    def path(self) -> str: ...                              # path to the progress file
```

**InputCollector** — interactive input for plan creation:
```python
class InputCollector(Protocol):
    def ask_question(self, question: str, options: list[str]) -> str: ...
    def ask_draft_review(self, question: str, plan_content: str) -> tuple[str, str]: ...
```

**GitChecker** — git-state inspection for review-loop optimization. This Protocol lives in `processor/runner.py` (not in the `git/` module) and is satisfied by `Service` — there is no separate `GitChecker` class in `cadence.git`:
```python
class GitChecker(Protocol):
    def head_hash(self) -> str: ...              # current HEAD commit hash
    def diff_fingerprint(self) -> str: ...       # working-tree diff hash
```

### Executors dataclass

Groups executor dependencies:
```python
@dataclass
class Executors:
    claude: Executor                     # required: task phase
    review_claude: Executor | None       # optional: separate model for reviews
```

If `review_claude` is not set (None), the same executor is used as for claude.

### Constructors

`Runner(cfg, log, holder)` — main constructor:
1. Creates a `ClaudeExecutor` from AppConfig (command, args, error/limit patterns, idle timeout, model)
2. If review_model differs from task_model, creates a separate review executor
3. Calls `Runner.from_executors()`

`Runner.from_executors(cfg, log, execs, holder)` — constructor with pre-built executors (for testing):
1. Sets `iteration_delay` from config or default (2.0 sec)
2. Sets `task_retry_count`, distinguishing explicit zero from "not set"
3. Sets `wait_on_limit` from AppConfig
4. If review_claude is None, copies the claude executor

### Setter methods

```
set_input_collector(c)   -- sets the input collector (plan creation mode)
set_git_checker(g)       -- sets the git checker (review loops)
set_break_event(event)   -- sets the break event (Ctrl+\, via threading.Event)
set_pause_handler(fn)    -- sets the pause/resume callback
```

## Execution mode methods

`run()` — entry point, dispatches on `cfg.mode`:

### run_full()

Full pipeline: tasks -> review_first -> review_loop.

```
1. PhaseTask: run_task_phase()
   - on UserAbortedError: log and return UserAbortedError
   - on error: wrap as "task phase: ..."

2. PhaseReview: run_claude_review(ReviewFirstPrompt)
   - section "claude review 0: all findings"
   - single pass, 4 agents
   - step 3 below is skipped when round 1 returns REVIEW_DONE

3. PhaseReview: run_claude_review_loop()
   - review loop (critical/major)
```

Requires: plan_file != ""

### run_review_only()

Review pipeline without task phase.

```
1. PhaseReview: run_claude_review(ReviewFirstPrompt)
2. PhaseReview: run_claude_review_loop()
   - skipped when round 1 returns REVIEW_DONE
```

### run_tasks_only()

Task phase only, no reviews.

```
1. PhaseTask: run_task_phase()
```

Requires: plan_file != ""

### run_plan_creation()

Interactive plan creation through Q&A with Claude.

```
max_plan_iterations = max(5, max_iterations // 5)
last_revision_feedback = ""

loop 1..max_plan_iterations:
  1. print_section(PlanIterationSection(i))
  2. Build prompt = build_plan_prompt()
  3. If last_revision_feedback present: append "PREVIOUS DRAFT FEEDBACK: ..."
  4. run_with_limit_retry(claude.run, prompt, "claude")

  Result handling:
  - Error: handle_pattern_match_error, return
  - FAILED signal: return error
  - PLAN_READY signal: return (success)
  - Session timeout: skip output parsing, retry (preserve last_revision_feedback)

  If not timed out:
  - Clear last_revision_feedback (if it was set)
  - Check PLAN_DRAFT: handle_plan_draft(output)
    - accept: continue (last_revision_feedback = "")
    - revise: continue (last_revision_feedback = feedback)
    - reject: raise UserRejectedPlanError
  - Check QUESTION: handle_plan_question(output)
    - question handled: continue
  - Otherwise: continue (wait for next iteration)
```

Requires: plan_description != "", input_collector is not None

## Execution phases (detailed)

### Task phase: run_task_phase()

Loop that executes tasks from the plan. Each iteration handles one task (one Task section).

```
prompt = replace_prompt_variables(TaskPrompt)
retry_count = 0

loop i = 1..max_iterations:
  1. Determine the task number:
     - task_num = next_plan_task_position() (from the plan, 1-indexed)
     - if 0: fall back to i (loop counter)
  2. print_section(TaskIterationSection(task_num))

  3. Create a break scope:
     - a separate threading.Event checked on break
     - cancels only the current session on Ctrl+\

  4. result = run_with_limit_retry(claude.run, prompt, "claude")

  5. Check for manual break:
     - is_break(): break_event.is_set() and the main thread is not cancelled

  6. On manual break:
     - break_event.clear() (clear pending signal)
     - if pause_handler is None or not pause_handler(): raise UserAbortedError
     - break_event.clear() (clear signal received during pause prompt)
     - i -= 1 (preserve iteration budget, restart the same task)
     - retry_count = 0
     - continue

  7. If result.error:
     - handle_pattern_match_error -> return
     - otherwise: raise error

  8. If COMPLETED signal:
     - has_uncompleted_tasks(): check the plan for remaining [ ]
     - if any uncompleted: warning, continue
     - if all done: return

  9. If FAILED signal:
     - if retry_count < task_retry_count: retry_count += 1, sleep, continue
     - otherwise: raise error

  10. Reset retry_count = 0
  11. sleep(iteration_delay)
```

Key properties:
- The prompt is the same on every iteration — Claude rereads the plan from the file
- next_plan_task_position() parses the plan and finds the first uncompleted task section
- has_uncompleted_tasks() checks Task sections only (not Success criteria/Overview/Context)
- For malformed plans (checkboxes without task headers): checks the whole file
- break-resume: the same task restarts with a fresh session, the plan is reread

### Review phase: run_claude_review(prompt)

A single review pass. Used for "review 0: all findings" with ReviewFirstPrompt (4 agents).

```
1. result = run_with_limit_retry(review_claude.run, prompt, "claude")
2. Error: handle_pattern_match_error -> raise
3. FAILED signal: raise error
4. REVIEW_DONE signal: ok
5. No REVIEW_DONE: warning "did not complete cleanly", continue
```

Sets `Runner.last_review_done` to `True` on `REVIEW_DONE` and `False` otherwise (including the "did not complete cleanly" path). `_run_review_pipeline` reads this flag to skip `run_claude_review_loop()` when round 1 finished cleanly.

### Review loop: run_claude_review_loop()

Iterative review loop with ReviewSecondPrompt (2 agents: critical/major findings only).

```
max_review_iterations = max(3, max_iterations // 10)

loop i = 1..max_review_iterations:
  1. print_section(ClaudeReviewSection(i, ": critical/major"))
  2. head_before = head_hash() (for no-commit detection)
  3. result = run_with_limit_retry(review_claude.run, ReviewSecondPrompt, "claude")
  4. Error: handle_pattern_match_error -> return
  5. FAILED signal: raise error
  6. REVIEW_DONE signal: return ("no more findings")
  7. last_session_timed_out: skip HEAD check, continue
  8. HEAD unchanged (head_after == head_before): return ("no changes detected")
  9. log "issues fixed, running another review iteration..."
  10. sleep(iteration_delay)

max iterations reached: log warning, return
```

No-commit detection logic: if Claude made no commits, there was nothing to fix. Session timeout bypasses this check (the session may have been killed before a commit landed).

## Session timeout and idle timeout

### Session timeout

`run_with_session_timeout(run, prompt, tool_name)`:

```
If session_timeout <= 0 or tool_name != "claude":
  result = run(prompt)
  If result.idle_timed_out and signal == "":
    last_session_timed_out = True  # treat as session timeout for review loops
  return result

# Run with timeout via threading.Timer
result = run(prompt)  # with a separate threading.Timer for session_timeout

If session timed out:
  result.error = None
  result.signal = ""  # cannot trust a partial session
  last_session_timed_out = True

If result.idle_timed_out and signal == "":
  last_session_timed_out = True  # idle timeout without a signal = session-timeout behavior
```

`last_session_timed_out` is used in:
- run_claude_review_loop(): skip the HEAD check and retry (do not confuse a timeout with "found nothing")

### Idle timeout

Implemented in `ClaudeExecutor.run()` (executor module), not in the processor.
The processor handles the result via the `result.idle_timed_out` flag.

If idle timeout fires without a signal: `last_session_timed_out = True`.
This is needed because an idle timeout without a signal looks like "found nothing" to review loops, when in fact the session "hung".

## Rate limit retry: run_with_limit_retry

```
loop:
  result = run_with_session_timeout(run, prompt, tool_name)

  If not error: return result
  If not LimitPatternError: return result (do not retry)
  If wait_on_limit <= 0: return result (no wait config)

  log "rate limit detected, waiting..."
  sleep_with_cancel(wait_on_limit)
  -- retry indefinitely
```

Order of checks:
1. LimitPatternError: if a wait is configured -> retry; if not -> return (will fall through to error handling)
2. PatternMatchError (regular error): return without retry
3. Other errors: return without retry

Retry indefinitely: the loop is not bounded by attempt count, only by cancellation.

## Break / pause / resume mechanism

### break_event (threading.Event)

`threading.Event`, set on receipt of a break signal (Ctrl+\ on Unix).
If break_event is None: the break mechanism is disabled.

### is_break() -> bool

Decides whether a break occurred: `break_event.is_set()` and the main thread is not cancelled.

### clear_break()

`break_event.clear()` — clears the event.
Called after pause+resume to prevent immediate cancellation of the next iteration.
Not called on regular iteration boundaries — preserves a legitimate Ctrl+\ between iterations.

### Behavior by phase

Task phase:
- break_event is checked on every iteration
- on break: kill the current session -> pause_handler -> resume (i -= 1) or abort (UserAbortedError)
- clear_break() after pause prompt (clears pending signal)

Claude review loop:
- No break check — the review loop is not interrupted by Ctrl+\
- (only cancellation via SIGINT/KeyboardInterrupt)

## Iteration calculation

Constants:
```
MIN_REVIEW_ITERATIONS    = 3     # minimum for claude review
REVIEW_ITERATION_DIVISOR = 10    # review iterations = max_iterations // 10
MIN_PLAN_ITERATIONS      = 5     # minimum for plan creation
PLAN_ITERATION_DIVISOR   = 5     # plan iterations = max_iterations // 5
```

When max_iterations = 50 (default):
- Task: 1..50
- Review: max(3, 50 // 10) = 5
- Plan: max(5, 50 // 5) = 10

## Prompt system

### Template variables

Defined in `processor/prompts.py`:

| Variable                       | Value                                              | Used in                       |
|-------------------------------|---------------------------------------------------|-------------------------------|
| `{{PLAN_FILE}}`               | path to the plan file or "(no plan file...)"      | all prompts                   |
| `{{PROGRESS_FILE}}`           | path to the progress file or "(no progress file...)" | all prompts                |
| `{{GOAL}}`                    | "implementation of plan at ..." or "current branch vs ..." | all prompts          |
| `{{DEFAULT_BRANCH}}`          | name of the default branch or "main"              | all prompts                   |
| `{{PLAN_DESCRIPTION}}`        | plan description (user input)                     | make_plan prompt              |
| `{{agent:name}}`              | expanded into Task tool instructions              | review prompts                |

### Replacement hierarchy

Two levels of replacement functions:

1. `replace_base_variables(prompt)` — base: PLAN_FILE, PROGRESS_FILE, GOAL, DEFAULT_BRANCH
2. `replace_prompt_variables(prompt)` — base + agent references + commit trailer

Order inside replace_prompt_variables:
1. replace_base_variables()
2. expand_agent_references() — expand agent references
3. append_commit_trailer_instruction()

### Agent expansion

`expand_agent_references(prompt)`:
- Regex: `\{\{agent:([a-zA-Z0-9_-]+)\}\}`
- Builds a dict name -> CustomAgent from app_config.custom_agents
- For each match:
  - If the agent is not found: warning, leave the reference as-is
  - If found: run replace_base_variables() on the agent's content, then format_agent_expansion()
  - Recursion is not supported: agent content does not pass through expand_agent_references()

```
format_agent_expansion(prompt, opts):
  subagent = opts.agent_type or "general-purpose"
  model_clause = f" with model={opts.model}" if opts.model is set
  -> "Use the Task tool{model_clause} to launch a {subagent} agent with this prompt:
      \"{prompt}\"
      Report findings only - no positive observations."
```

### Commit trailer

`append_commit_trailer_instruction(prompt)`:
- If app_config.commit_trailer is empty: return prompt unchanged
- Otherwise: append the instruction "When making git commits, add the following trailer..."
- Called ONCE on the final assembled prompt (not inside agent expansion)

### Build functions for prompts

| Function                        | Prompt source              | Special variables               |
|--------------------------------|----------------------------|---------------------------------|
| build_plan_prompt()             | MakePlanPrompt             | {{PLAN_DESCRIPTION}}            |

## Signal parsing

Defined in `processor/signals.py`.

### Helper functions

```
is_review_done(signal)  -> signal == "<<<CADENCE:REVIEW_DONE>>>"
is_plan_ready(signal)   -> signal == "<<<CADENCE:PLAN_READY>>>"
```

### QUESTION signal

Format in output:
```
<<<CADENCE:QUESTION>>>
{"question": "...", "options": ["...", "..."]}
<<<CADENCE:END>>>
```

`parse_question_payload(output)`:
1. Check for the `<<<CADENCE:QUESTION>>>` substring
2. Regex-extract the JSON between the QUESTION and END markers
3. json.loads into a QuestionPayload dataclass
4. Validation: question != "", options not empty

### PLAN_DRAFT signal

Format in output:
```
<<<CADENCE:PLAN_DRAFT>>>
# Plan content...
<<<CADENCE:END>>>
```

`parse_plan_draft_payload(output)`:
1. Check for the `<<<CADENCE:PLAN_DRAFT>>>` substring
2. Regex-extract the content between the PLAN_DRAFT and END markers
3. strip(), check non-empty

### Draft review handling

`handle_plan_draft(output) -> DraftReviewResult`:
1. parse_plan_draft_payload(output)
2. If no draft: return DraftReviewResult(handled=False)
3. input_collector.ask_draft_review("Review the plan draft", plan_content)
4. log_draft_review(action, feedback)
5. Match action:
   - "accept": return DraftReviewResult(handled=True) (continue to PLAN_READY)
   - "revise": return DraftReviewResult(handled=True, feedback=feedback)
   - "reject": return DraftReviewResult(handled=True, error=UserRejectedPlanError)

## Helper functions

### Plan file resolution

`resolve_plan_file_path()`:
1. If plan_file is empty: return ""
2. Check Path(plan_file).exists():
   - exists: return plan_file
   - permission error: return plan_file
3. Check the sibling file with the `-completed` suffix (e.g. `plan.md` -> `plan-completed.md`, `preprompt` -> `preprompt-completed`)
4. Fallback: return the original plan_file

### has_uncompleted_tasks()

Checks for uncompleted checkboxes inside Task sections of the plan:
1. resolve_plan_file_path()
2. parse_plan_file()
3. Iterate over tasks: has_uncompleted_actionable_work()
4. Malformed plans (no task headers): file_has_uncompleted_checkbox()

Ignores checkboxes in Success criteria, Overview, and Context — for a correct ALL_TASKS_DONE.

### next_plan_task_position()

Returns the 1-indexed position of the first uncompleted task:
1. parse_plan_file()
2. Iterate over tasks: has_uncompleted_actionable_work()
3. Return i + 1 (1-indexed) or 0 if none

### sleep_with_cancel(duration)

Cancelable sleep via threading.Event.wait(timeout) or equivalent.

## Sentinel errors

```python
class UserAbortedError(Exception):
    """break + decline resume"""
    pass

class UserRejectedPlanError(Exception):
    """reject draft in plan creation"""
    pass
```

## Python port considerations

### Concurrency model

Python 3.14+, sync + threading (not asyncio). For cancellation and timeouts:
- `threading.Event` as a replacement for a break channel (Go `chan struct{}`)
- `threading.Timer` for session timeout and idle timeout
- Cancellation by killing the process (subprocess.Popen.terminate/kill)

### Executor interface

Simple interface: `run(prompt) -> Result`. In Python:
- Protocol class
- `def run(self, prompt: str) -> Result` (synchronous)
- Cancellation via process.terminate() / process.kill()

### Signal detection

Substring search in the output. In Python: trivial via `str.find()` / `re.search()`.

### Timer management

`threading.Timer` with cancel/restart for idle timeout. On every output line: timer.cancel() + create a new timer.

### Template system

Simple string replacement with `str.replace()`. Agent expansion regex: `re.sub()` with a callback function.

### Plan parsing dependency

Runner calls `parse_plan_file()` for has_uncompleted_tasks() and next_plan_task_position(). These calls happen synchronously in the loop; the file is reread on every iteration.
