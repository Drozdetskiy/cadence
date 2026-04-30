# Make --impl Flag Auto-Execute Task Phase After Plan Creation

## Overview
The `--impl` flag is already plumbed through CLI parsing and `run_plan_mode` but currently only prints "(implementation not available in v0.1)" after successful plan creation. The rest of rlx (branch creation, task execution, review_first, review_loop, finalize) is fully functional via `run_task_mode`. This change wires `--impl` to invoke `run_task_mode` on the derived plan path immediately after a successful `run_plan_mode`, so `rlx --plan <file> --impl` performs the full pipeline in one command.

## Context
- Files involved:
  - `src/rlx/cli.py` — `run_plan_mode()` currently prints the not-available message at line 264; `run_task_mode()` already exists and accepts a plan file path. The chaining must happen after `log.close(success=...)` in `run_plan_mode` so the plan logger is fully closed before the task logger opens.
  - `tests/test_cli.py` — `TestRunPlanModeImplFlag` (lines 473–562) currently asserts the not-available message; needs to be replaced with assertions that `run_task_mode` is invoked with the derived plan path when `impl=True`, and not invoked when `impl=False`.
  - `CLAUDE.md` — top-level description says "The `--impl` flag stores intent for auto-implementation after plan creation (not yet implemented)"; needs to reflect the new behavior.
- Related patterns:
  - `derive_plan_path(prompt_file)` already produces the path Claude writes the plan to.
  - `run_task_mode` already validates the file exists, ensures git repo, creates a branch, and runs the full task → review → finalize pipeline.
- Dependencies: none; both functions live in the same module.

## Development Approach
- Testing approach: Regular (code first, then tests)
- Complete each task fully before moving to the next
- CRITICAL: every task MUST include new/updated tests
- CRITICAL: all tests must pass before starting next task

## Implementation Steps

### Task 1: Wire --impl to run_task_mode in run_plan_mode

Files:
- Modify: `src/rlx/cli.py`
- Modify: `tests/test_cli.py`

- [x] In `run_plan_mode`, capture the derived plan path into a local variable inside the `if run_success:` block (alongside the existing `typer.echo(f"run: rlx --task {plan_path}")`) and remove the `if impl: typer.echo("(implementation not available in v0.1)")` branch
- [x] After the `try/except/finally` (so `log.close(success=...)` has already run), add: `if impl and run_success and plan_path is not None: run_task_mode(Path(plan_path))`
- [x] Initialize `plan_path: str | None = None` before the `try` so it is accessible after the `finally`
- [x] Update `tests/test_cli.py::TestRunPlanModeImplFlag::test_impl_flag_shows_not_available`: rename to `test_impl_flag_chains_to_task_mode`, patch `rlx.cli.run_task_mode`, assert it is called once with a `Path` matching `derive_plan_path(prompt_file)`, and assert "not available in v0.1" is NOT in echo output
- [x] Update `tests/test_cli.py::TestRunPlanModeImplFlag::test_no_impl_flag_no_not_available_message`: keep the assertion that "rlx --task" is in echo output, and additionally assert `run_task_mode` is NOT called when `impl=False` (patch it and check `mock.assert_not_called()`)
- [x] Add a new test `test_impl_flag_does_not_chain_on_plan_failure`: simulate `Runner.run()` returning `False`, assert `run_task_mode` is NOT called even when `impl=True`
- [x] Run `pytest tests/ -v` — must pass before task 2

### Task 2: Verify acceptance criteria

- [x] Run full test suite: `pytest tests/ -v` (506 passed)
- [x] Run linter: `ruff check src/ tests/` (clean)
- [x] Run type checker: `mypy src/` (no issues, 27 source files)
- [x] Verify in CLAUDE.md description that `--impl` is no longer described as "not yet implemented" (currently still says it; CLAUDE.md update is sequenced into Task 3)

### Task 3: Update documentation

- [x] Update `CLAUDE.md`: change the `--impl` description from "stores intent for auto-implementation after plan creation (not yet implemented)" to reflect that it now chains directly into the full task pipeline after plan creation
- [x] Move this plan to `docs/plans/completed/`
