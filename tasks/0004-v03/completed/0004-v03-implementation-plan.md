# rlx v0.3 — Review Pipeline Mode (`--review` + full `--task` pipeline)

## Overview

Implement v0.3 scope: add the `rlx --review` mode (review-only of the current branch) and extend `rlx --task <file>` from a tasks-only flow into the full pipeline (tasks → review_first → review_loop → finalize). This introduces the agents subsystem, three new prompts (review_first, review_second, finalize), five embedded agent files (quality, implementation, testing, simplification, documentation), review/finalize phases in the Runner, an optional second ClaudeExecutor for a distinct review model, and the CLI wiring for the new mode.

The specification is `docs/reference/04-processor.md` (run_full/run_review_only/run_claude_review/run_claude_review_loop/run_finalize/last_session_timed_out semantics), `docs/reference/08-prompts.md` (verbatim text of review_first.txt/review_second.txt/finalize.txt and `expand_agent_references` / `format_agent_expansion` / `replace_prompt_variables` / `build_review_first_prompt` / `build_review_second_prompt` / `build_finalize_prompt` contracts), and `docs/reference/09-agents.md` (verbatim text of the 5 agents, frontmatter schema, loader behavior). Prompt/agent files MUST be copied verbatim from those documents.

## Context

Files involved (existing, to modify):
- `src/rlx/cli.py` — remove --review stub, add `run_review_mode`, upgrade `run_task_mode` to full pipeline, validate `--review` incompat with `--impl`, wire optional `review_claude` executor.
- `src/rlx/config.py` — already has `finalize_enabled`, `review_model`, `commit_trailer`; no new fields needed.
- `src/rlx/status.py` — already has `Mode.REVIEW`, `PhaseReview`, `PhaseFinalize`, `SignalReviewDone`, `new_claude_review_section`; add a small `new_finalize_section` helper (optional) or inline the section label.
- `src/rlx/processor/signals.py` — add `is_review_done`, `is_task_failed`, `is_all_tasks_done` helpers.
- `src/rlx/processor/prompts.py` — add `expand_agent_references`, `format_agent_expansion`, `replace_prompt_variables`, `build_review_first_prompt`, `build_review_second_prompt`, `build_finalize_prompt`.
- `src/rlx/processor/runner.py` — extend `Dependencies` with `review_executor: Executor | None`; add `run_review_only`, update `run_full` to task→review_first→review_loop→finalize, add `run_claude_review`, `run_claude_review_loop`, `run_finalize`; add `Mode.REVIEW` dispatch.
- `src/rlx/git/__init__.py`, `backend.py`, `service.py` — already implement `head_hash()` + `diff_fingerprint()` on both the module-level `GitChecker` class and `Service`; no new behavior needed, only confirm coverage.

Files involved (new, to create):
- `src/rlx/processor/agents.py` — agent loader, frontmatter parser, model normalization, `load_agent(name, local_dir) -> AgentDef`.
- `src/rlx/defaults/agents/__init__.py` — empty marker for importlib.resources.
- `src/rlx/defaults/agents/quality.txt`, `implementation.txt`, `testing.txt`, `simplification.txt`, `documentation.txt` — verbatim from `docs/reference/09-agents.md`.
- `src/rlx/defaults/prompts/review_first.txt`, `review_second.txt`, `finalize.txt` — verbatim from `docs/reference/08-prompts.md`.
- `tests/test_agents.py` — loader, frontmatter, normalization, fallback.
- Test additions in `tests/test_prompts.py`, `tests/test_processor.py`, `tests/test_cli.py`, `tests/test_signals.py`.

Related patterns:
- Prompt loader fallback (local `.rlx/prompts/<name>.txt` → embedded `rlx.defaults.prompts`) in `src/rlx/processor/prompts.py::load_prompt` — mirror this for agents.
- Protocol-based dependency injection in `Runner` (`Dependencies` dataclass in `runner.py`).
- Config is already wired (finalize_enabled/review_model/commit_trailer). No TOML changes needed.
- Executor creation pattern in `cli.py::run_plan_mode` / `run_task_mode` (activity_handler, output_handler, patterns, idle_timeout).

Dependencies:
- No new external deps. YAML frontmatter parsing is simple enough (`---` delimited, `key: value` lines) — write a minimal parser; do not pull in pyyaml.

## Development Approach

- Testing approach: Regular (code first, then tests), same as v0.1/v0.2.
- Complete each task fully (code + tests + green `pdm run pytest` / `pdm run ruff check src/ tests/` / `pdm run mypy src/`) before moving to the next.
- Never run real `claude` or require real git repos in tests; mock `Executor` / `GitChecker` protocols; use `tmp_path` fixtures for file-based behaviors.
- Prompt and agent files MUST be copied VERBATIM from `docs/reference/08-prompts.md` (sections for review_first.txt, review_second.txt, finalize.txt) and `docs/reference/09-agents.md` (sections for each agent). Do not paraphrase.
- Follow the existing strict-mypy + Protocol conventions: all new Runner helpers operate via `Executor` and `GitChecker` protocols, no concrete imports from implementations.
- CRITICAL: every task MUST include new/updated tests.
- CRITICAL: all tests must pass before starting the next task.

## Implementation Steps

### Task 1: Agents module + embedded agent defaults

**Files:**
- Create: `src/rlx/processor/agents.py`
- Create: `src/rlx/defaults/agents/__init__.py`
- Create: `src/rlx/defaults/agents/quality.txt`
- Create: `src/rlx/defaults/agents/implementation.txt`
- Create: `src/rlx/defaults/agents/testing.txt`
- Create: `src/rlx/defaults/agents/simplification.txt`
- Create: `src/rlx/defaults/agents/documentation.txt`
- Create: `tests/test_agents.py`

- [x] create `src/rlx/defaults/agents/__init__.py` (empty) so it ships as a package resource
- [x] create the five agent .txt files, copying bodies VERBATIM from `docs/reference/09-agents.md` (no frontmatter needed by default)
- [x] create `src/rlx/processor/agents.py` with:
  - `@dataclass(frozen=True) class AgentDef: name: str; body: str; model: str = ""; agent_type: str = "general-purpose"`
  - `_ALLOWED_MODELS = {"haiku", "sonnet", "opus"}`
  - `_normalize_model(value: str) -> str` — maps short names as-is, long IDs like `claude-sonnet-4-5-20250929` by substring match (`"sonnet" in value`, `"haiku" in value`, `"opus" in value`); returns `""` when unmatched
  - `_parse_frontmatter(text: str) -> tuple[dict[str, str], str]` — splits on leading `---\n...\n---\n`, returns `(fields, body)`; if no frontmatter or malformed, returns `({}, text)`
  - `load_agent(name: str, *, local_dir: Path | None = None, warn: Callable[[str], None] | None = None) -> AgentDef | None` — tries `<local_dir>/agents/<name>.txt` first, then `importlib.resources.files("rlx.defaults.agents").joinpath(f"{name}.txt")`; on missing file returns `None`; parses frontmatter, normalizes `model` (invalid values → drop + `warn("invalid model %r for agent %s, ignoring", value, name)`), defaults `agent` to `"general-purpose"`
  - export only public symbols through `__all__`
- [x] in `tests/test_agents.py`, write tests for:
  - loader fallback: local file takes precedence over embedded (use `tmp_path` with `.rlx/agents/<name>.txt`)
  - frontmatter parsed: `model: sonnet`, `agent: code-reviewer` applied
  - long model ID normalization: `claude-sonnet-4-5-20250929` → `sonnet`, `claude-haiku-4-5-20251001` → `haiku`
  - invalid `model` value → warn called + `AgentDef.model == ""`, body preserved
  - missing agent returns `None` (caller decides how to handle)
  - bodies of the 5 default agents load without frontmatter and carry the expected first line (assert on a stable substring)
- [x] run `pdm run pytest tests/test_agents.py -v` — must pass
- [x] run `pdm run ruff check src/ tests/` — must pass
- [x] run `pdm run mypy src/` — must pass

### Task 2: Prompts extension + embedded review/finalize prompts

**Files:**
- Modify: `src/rlx/processor/prompts.py`
- Create: `src/rlx/defaults/prompts/review_first.txt`
- Create: `src/rlx/defaults/prompts/review_second.txt`
- Create: `src/rlx/defaults/prompts/finalize.txt`
- Modify: `tests/test_prompts.py`

- [x] create the three prompt files, copying text VERBATIM from `docs/reference/08-prompts.md` (sections "review_first.txt", "review_second.txt", "finalize.txt")
- [x] extend `src/rlx/processor/prompts.py`:
  - add `_AGENT_REF_RE = re.compile(r"\{\{agent:([a-zA-Z0-9_-]+)\}\}")`
  - add `format_agent_expansion(prompt_body: str, *, model: str, agent_type: str) -> str` — emits: `Use the Task tool[ with model=<model>] to launch a <agent_type> agent with this prompt:\n"<body>"\n\nReport findings only - no positive observations.` (square-bracket clause only when `model` is non-empty)
  - add `expand_agent_references(prompt: str, *, local_dir: Path | None, warn: Callable[[str], None] | None, base_vars: dict[str, str]) -> str` — for each regex match: call `load_agent(name, local_dir=local_dir, warn=warn)`; if `None`, log warning via `warn` and leave the `{{agent:<name>}}` marker in place; otherwise `replace_base_variables` on the body (NO recursion — do NOT re-run `expand_agent_references`) using the supplied `base_vars`, then `format_agent_expansion(...)`. Replace the match with the expansion.
  - add `replace_prompt_variables(prompt: str, *, plan_file: str, progress_file: str, goal: str, default_branch: str, plans_dir: str, commit_trailer: str, local_dir: Path | None, warn: Callable[[str], None] | None = None) -> str` — composes: `replace_base_variables(...)` → `expand_agent_references(...)` (passing the same base vars so agent bodies also get `{{DEFAULT_BRANCH}}` / `{{GOAL}}` resolved) → `append_commit_trailer_instruction(..., commit_trailer)`. Trailer is appended EXACTLY ONCE at the end of the outer prompt; it is NOT appended inside agent bodies.
  - add `build_review_first_prompt(*, local_dir, plan_file, progress_file, default_branch, commit_trailer, warn=None) -> str` — loads `review_first` via `load_prompt`, goal defaults to `f"review of branch vs {default_branch}"` when no plan_file, else `f"implementation of plan at {plan_file}"`; calls `replace_prompt_variables(...)`
  - add `build_review_second_prompt(...)` — same as above using `review_second`
  - add `build_finalize_prompt(...)` — loads `finalize` via `load_prompt`; since finalize has no signals and no agent refs, it still goes through `replace_prompt_variables` for consistency (regex will no-op when no `{{agent:*}}` markers are present)
  - update `__all__`
- [x] extend `tests/test_prompts.py` with:
  - `format_agent_expansion` with and without model
  - `expand_agent_references` match/miss (missing agent warns + leaves marker untouched); recursion guard (agent body containing `{{agent:X}}` is NOT re-expanded — assert literal markers remain)
  - `replace_prompt_variables` appends commit trailer exactly once (count `commit_trailer` substring and also assert that it does NOT appear inside the expanded agent body)
  - `build_review_first_prompt` expands all 5 agent refs from embedded defaults, substitutes `{{DEFAULT_BRANCH}}` / `{{GOAL}}` / `{{PROGRESS_FILE}}`, trailer appears once when configured, none when empty
  - `build_review_second_prompt` expands the 2 agent refs (quality + implementation)
  - `build_finalize_prompt` loads and substitutes without errors; no signals expected
  - local override: `tmp_path/.rlx/agents/quality.txt` with different body overrides embedded default in a review prompt
- [x] run `pdm run pytest tests/test_prompts.py tests/test_agents.py -v` — must pass
- [x] run `pdm run ruff check src/ tests/` and `pdm run mypy src/` — must pass

### Task 3: Runner — review phases, finalize, and Mode.REVIEW dispatch

**Files:**
- Modify: `src/rlx/processor/signals.py`
- Modify: `src/rlx/processor/runner.py`
- Modify: `src/rlx/status.py` (minor: add `new_finalize_section` helper if not already present; otherwise inline)
- Modify: `tests/test_signals.py`
- Modify: `tests/test_processor.py`

- [x] extend `src/rlx/processor/signals.py`:
  - `is_review_done(signal: str) -> bool` → `signal == SignalReviewDone`
  - `is_task_failed(signal: str) -> bool` → `signal == SignalFailed`
  - `is_all_tasks_done(signal: str) -> bool` → `signal == SignalCompleted`
- [x] optional: add `new_finalize_section() -> Section` in `src/rlx/status.py` returning the "finalize step" label (or reuse `new_generic_section("finalize step")`); choose one and stay consistent
- [x] extend `src/rlx/processor/runner.py`:
  - extend `Dependencies` with `review_executor: Executor | None = None`. Add `_review_executor` property returning `self._deps.review_executor or self._deps.executor`
  - add module constants `MIN_REVIEW_ITERATIONS = 3` and `REVIEW_ITERATION_DIVISOR = 10`
  - update `run()` dispatch: add `Mode.REVIEW` → `run_review_only()`; keep ValueError fallback for anything else
  - update `run_full()` to: `run_task_phase()` → if success, switch phase to `PhaseReview` and call `run_claude_review(build_review_first_prompt(...))` then `run_claude_review_loop()`; finally `run_finalize()`. Any failure in task phase short-circuits. Review/loop errors propagate unless they are `PatternMatchError`/`LimitPatternError` (already handled by `_handle_pattern_match_error` returning False) — follow the same shape as `run_task_phase`.
  - add `run_review_only()` — requires git_checker, sets `PhaseReview`, same sequence minus tasks phase, returns bool
  - add `run_claude_review(prompt: str) -> bool` — calls `_run_with_limit_retry(self._review_executor.run, prompt)`; on `PatternMatchError`/`LimitPatternError` → log + return False; on `SignalFailed` → log error + raise `RuntimeError("review failed")`; on `SignalReviewDone` → log "review completed, no issues found" + return True; otherwise warn "review did not complete cleanly" + return True (continues pipeline). Use `new_claude_review_section(0, ": all findings")` for the section header.
  - add `run_claude_review_loop() -> bool` — computes `max_review_iterations = max(MIN_REVIEW_ITERATIONS, self._app.max_iterations // REVIEW_ITERATION_DIVISOR)`; builds `build_review_second_prompt` once, loops 1..N:
    - print `new_claude_review_section(i, ": critical/major")`
    - capture `head_before = self._git_checker.head_hash()` if `self._git_checker` else `""`
    - run via `_run_with_limit_retry(self._review_executor.run, ...)`
    - on `PatternMatchError`/`LimitPatternError` → log + return False
    - on `SignalFailed` → raise `RuntimeError`
    - on `SignalReviewDone` → log + return True
    - no-commit check: if `self.last_session_timed_out` → skip HEAD check, continue; else if `self._git_checker` and `self._git_checker.head_hash() == head_before` → log "no changes detected, stopping review loop" + return True
    - log "issues fixed, running another review iteration" + sleep + continue
    - after loop exhausted → warn "max review iterations reached" + return True (best-effort)
  - add `run_finalize() -> None` — best-effort:
    - return immediately if not `self._app.finalize_enabled`
    - set `PhaseFinalize`, print section, build prompt via `build_finalize_prompt(...)`
    - call `_run_with_limit_retry(self._review_executor.run, prompt)` inside a try/except
    - let `KeyboardInterrupt` propagate; every other exception → `log.warn("finalize error: %s", exc)`, return
    - on `result.error` that is `PatternMatchError`/`LimitPatternError` → handle via `_handle_pattern_match_error` and return
    - on other `result.error` → log.warn and return (do NOT raise)
    - on `SignalFailed` → log.warn("finalize reported failure") and return
    - otherwise → log "finalize step completed"
  - update `__all__`
- [x] extend `tests/test_signals.py` with coverage for the three new helpers
- [x] extend `tests/test_processor.py` with:
  - `run_claude_review`: REVIEW_DONE path returns True; FAILED path raises RuntimeError; no-signal path returns True with warning; PatternMatchError returns False
  - `run_claude_review_loop`: REVIEW_DONE on first iteration returns True; no-commit detection (same head_hash) stops the loop and returns True; `last_session_timed_out = True` skips the head-check and continues; iteration cap respected
  - `run_finalize`: disabled by default (no executor call); enabled + success returns normally; enabled + executor raises generic exception → swallowed + warn; enabled + KeyboardInterrupt → propagates; enabled + PatternMatchError → swallowed
  - `run_full`: task phase success triggers review_first → loop → finalize in order (assert call order on a single `MagicMock` executor via `call_args_list`); failed task phase short-circuits (no review executor calls)
  - `run_review_only`: calls review_first + loop + finalize; does NOT invoke task phase
  - Mode.REVIEW dispatch via `run()` routes to `run_review_only`
  - when `review_executor` is set, review/finalize calls use it instead of `executor` (assert two separate mocks)
- [x] run `pdm run pytest -v` — all tests (old + new) must pass
- [x] run `pdm run ruff check src/ tests/` and `pdm run mypy src/` — must pass

### Task 4: CLI — `run_review_mode` + upgrade `run_task_mode` to full pipeline

**Files:**
- Modify: `src/rlx/cli.py`
- Modify: `tests/test_cli.py`

- [x] add `--review` + `--impl` mutual-exclusivity check in `main()` (before `determine_mode`): error if both set, same pattern as the existing `--impl requires --plan` guard
- [x] replace the `Mode.REVIEW` stub (currently prints "error: --review mode not implemented in v0.1" and exits) with a call to `run_review_mode()`
- [x] implement `run_review_mode() -> None`:
  - no `plan_file`, no `plan_description`, no branch creation, no `move_plan_to_completed`
  - `load_config`, `check_claude_dep`, `_ensure_git_repo`
  - `Service.open(path=".", log=_StderrLogger(), command=vcs)`, `set_commit_trailer(cfg.commit_trailer)`, resolve `default_branch`
  - build Logger via a new `_build_review_logger(colors, holder, branch)` helper (mode=Mode.REVIEW, plan_file="", branch=current branch)
  - capture `branch = git_svc.current_branch()` from the existing repo
  - build primary `ClaudeExecutor` (model=cfg.claude_model) and, when `cfg.review_model` is non-empty and `!= cfg.claude_model`, build a second `ClaudeExecutor` for review/finalize (model=cfg.review_model, same handlers/patterns/idle_timeout); otherwise reuse the primary one
  - build `Dependencies(executor=claude, review_executor=review_claude_or_none, ...)`
  - build `RunContext(mode=Mode.REVIEW, plan_file="", plan_description="", progress_path=log.path, default_branch=default_branch, local_dir=local_dir)`
  - install sigquit / break handler / pause handler / git_checker exactly as `run_task_mode` does
  - on success: `diff_stats = git_svc_for_log.diff_stats(default_branch)`, `display_stats(stats, log.elapsed(), branch)` — NO `move_plan_to_completed`
- [x] update `run_task_mode` to pass the optional `review_executor`:
  - construct the second `ClaudeExecutor` when `cfg.review_model` is non-empty and differs from `cfg.claude_model`
  - include it in `Dependencies(..., review_executor=...)`
  - NO other flow change — Runner itself now handles the full pipeline (task → review → loop → finalize) via `run_full`
- [x] extract a tiny helper `_build_review_executor(cfg, activity_handler, output_handler, idle_timeout) -> ClaudeExecutor | None` to avoid duplication between `run_review_mode` and `run_task_mode`; returns `None` when `cfg.review_model == ""` or equal to `cfg.claude_model`
- [x] extend `tests/test_cli.py`:
  - `determine_mode(review=True)` returns `Mode.REVIEW` (already likely covered; confirm)
  - `--review` + `--impl` combination emits "error: --review is incompatible with --impl" and exits non-zero
  - `--review` + `--task` still rejected (already covered); re-confirm
  - `run_review_mode` smoke test: patch `Runner.run` to return True, patch `ClaudeExecutor` + `Service`, assert no plan_file / no move_plan_to_completed calls, assert `display_stats` invoked
  - `review_model` distinct from `claude_model` builds two executors and passes `review_executor` through `Dependencies`
  - `review_model` equal to `claude_model` (or empty) → single executor shared, `Dependencies.review_executor is None`
  - `run_task_mode` now wires `Dependencies.review_executor` identically based on config
- [x] run `pdm run pytest -v` — full suite must pass
- [x] run `pdm run ruff check src/ tests/` and `pdm run mypy src/` — must pass
- [x] run `rlx --version` — prints version string

### Task 5: Verify acceptance criteria

- [x] run `pdm run pytest` — full suite passes, including all new tests
- [x] run `pdm run ruff check src/ tests/` — zero errors
- [x] run `pdm run mypy src/` — strict type check passes
- [x] run `make check` — confirms all three of the above
- [x] run `rlx --version` — prints `rlx <version>`
- [x] run `rlx --review --impl` in any directory — exits non-zero with a clear incompatibility error
- [x] verify `ls src/rlx/defaults/agents/` lists all five agent files and `ls src/rlx/defaults/prompts/` lists `review_first.txt`, `review_second.txt`, `finalize.txt` alongside the existing defaults
- [x] verify test coverage across the new code paths: `run_claude_review` (3 paths), `run_claude_review_loop` (REVIEW_DONE, no-commit, timed-out skip, iteration cap), `run_finalize` (disabled, success, swallowed error, KeyboardInterrupt), `expand_agent_references` (match, miss, recursion guard), `load_agent` (local, embedded, frontmatter, normalization, invalid model), `Mode.REVIEW` dispatch

### Task 6: Update documentation and move plan

- [x] update `README.md` with `--review` usage (one short section, example command)
- [x] update `CLAUDE.md`: add `processor/agents.py`, `defaults/agents/`, and the three new prompt files to the package structure; mention the review/finalize phase addition to Runner; update the v0.3 description at the top
- [x] move this plan from `tasks/0004-v03/0004-v03-implementation-plan.md` to `tasks/0004-v03/completed/0004-v03-implementation-plan.md`
