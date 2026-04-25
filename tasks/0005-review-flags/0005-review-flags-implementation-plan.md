# rlx --review: `--base` flag for target branch override

## Overview

Add a `--base <branch>` CLI flag that overrides the base branch used for the review diff in `rlx --review` mode. The flag has effect only for the current run and does not modify `.rlx/config.toml`. Resolution priority becomes: `--base` > `cfg.default_branch` > `git_svc.get_default_branch()`. The flag is rejected with `--plan` and `--task` (mirrors the existing `--impl` validation pattern).

## Context

- Files involved:
  - Modify: `src/rlx/cli.py` — add option, validation, plumb into `run_review_mode`
  - Modify: `tests/test_cli.py` — add CLI parsing and resolution tests
- Related patterns:
  - Existing typer options declared at module scope: `_PLAN_OPT`, `_TASK_OPT`, `_REVIEW_OPT`, `_IMPL_OPT`, `_VERSION_OPT` (cli.py:544-548)
  - Mutually-exclusive flag validation in `main()` (cli.py:563-586): early-fail with `typer.echo(..., err=True)` + `raise SystemExit(1)`
  - `run_review_mode()` body that resolves `default_branch` (cli.py:451), prints `branch:` (cli.py:458), and passes `default_branch` into `RunContext` and `diff_stats(default_branch)` (cli.py:499, 515)
  - Test patterns in `tests/test_cli.py`:
    - `TestMainCommand` — `CliRunner` invocations for flag parsing, exit codes, and error messages (e.g. `test_review_with_impl_errors`, `test_impl_with_task_errors`)
    - `TestRunReviewMode` — full mock wiring: `Service`, `ClaudeExecutor`, `Logger`, `Runner`, `is_git_repo`, `load_config`, `detect_local_dir`, `check_claude_dep`, `_install_sigquit`, `TerminalCollector` (cli.py test L809-1095)
- Dependencies: none new.

## Development Approach

- Testing approach: Regular (code first, then tests) — implementation is small and mechanical.
- Complete each task fully before moving to the next.
- CRITICAL: every task MUST include new/updated tests.
- CRITICAL: all tests must pass before starting next task.
- Do not install/reinstall the package; do not run `rlx` itself. Validate only via `pytest`, `ruff`, `mypy`, plus `CliRunner` for CLI parsing checks.

## Implementation Steps

### Task 1: Add `--base` option, validation, and plumb into `run_review_mode`

**Files:**
- Modify: `src/rlx/cli.py`

- [x] Declare module-scope option near the other options (after `_IMPL_OPT`):
      `_BASE_OPT: str | None = typer.Option(None, "--base", help="Base branch for review diff (overrides config default_branch)")`
- [x] Add `base: str | None = _BASE_OPT` parameter to `main()`
- [x] In `main()`, after the `--impl`/`--review` validation block, add:
      if `base is not None` and not `review` → `typer.echo("error: --base is only valid with --review", err=True)` then `raise SystemExit(1)`
- [x] Pass `base` into `run_review_mode(base)` from the `Mode.REVIEW` dispatch branch
- [x] Update `run_review_mode` signature to `def run_review_mode(base: str | None = None) -> None:`
- [x] Inside `run_review_mode`, change `default_branch = cfg.default_branch or git_svc.get_default_branch()` to `default_branch = base or cfg.default_branch or git_svc.get_default_branch()`
- [x] Add `log.print("base: %s", default_branch)` immediately after `log.print("branch: %s", branch)`
- [x] Do NOT add an existence check for the branch (out of scope per prompt; let git surface the error at `diff_stats` time)
- [x] Update existing test `test_review_flag_calls_run_review_mode` to assert `mock_run.assert_called_once_with(None)` (since the signature gained a positional arg)
- [x] Update / extend `TestRunReviewMode` mock wiring tests so they keep passing (they already invoke `run_review_mode()` without args; default `base=None` keeps them green — verify, no change required if signature uses default)
- [x] run `pdm run pytest tests/test_cli.py -v` — must pass
- [x] run `pdm run ruff check src/ tests/` — must pass
- [x] run `pdm run mypy src/` — must pass

### Task 2: CLI parsing and validation tests for `--base`

**Files:**
- Modify: `tests/test_cli.py`

- [x] In `TestMainCommand`, add `test_base_with_review_passes`: patch `rlx.cli.run_review_mode`, invoke `["--review", "--base", "develop"]`, assert `exit_code == 0` and `mock_run.assert_called_once_with("develop")`
- [x] Add `test_base_without_review_errors`: invoke `["--base", "develop"]`, assert `exit_code != 0` and `"--base is only valid with --review"` in `result.output`
- [x] Add `test_base_with_plan_errors`: write a tmp prompt file, invoke `["--plan", str(f), "--base", "develop"]`, assert `exit_code != 0` and the same error message in output
- [x] Add `test_base_with_task_errors`: write a tmp plan file, invoke `["--task", str(f), "--base", "develop"]`, assert `exit_code != 0` and the same error message in output
- [x] run `pdm run pytest tests/test_cli.py::TestMainCommand -v` — must pass
- [x] run `pdm run ruff check src/ tests/` — must pass
- [x] run `pdm run mypy src/` — must pass

### Task 3: Resolution priority tests for `run_review_mode`

**Files:**
- Modify: `tests/test_cli.py`

- [x] In `TestRunReviewMode`, add `test_base_arg_overrides_config_and_autodetect`:
      - `Config(iteration_delay_ms=0, default_branch="main")`
      - `mock_svc.get_default_branch.return_value = "should-not-be-used"`
      - invoke `run_review_mode(base="develop")`
      - assert `mock_svc.diff_stats.assert_called_once_with("develop")`
      - assert `mock_svc.get_default_branch.assert_not_called()`
      - assert `mock_log.print` was called with `("base: %s", "develop")`
- [x] Add `test_base_none_uses_config_default_branch`:
      - `Config(iteration_delay_ms=0, default_branch="trunk")`
      - invoke `run_review_mode(base=None)`
      - assert `diff_stats` called with `"trunk"`
      - assert `get_default_branch` not called
      - assert `log.print` was called with `("base: %s", "trunk")`
- [x] Add `test_base_none_falls_back_to_autodetect_when_config_empty`:
      - `Config(iteration_delay_ms=0, default_branch="")` (or omit so it's empty)
      - `mock_svc.get_default_branch.return_value = "main"`
      - invoke `run_review_mode()`
      - assert `diff_stats` called with `"main"`
      - assert `get_default_branch` was called once
      - assert `log.print` was called with `("base: %s", "main")` — regression-guards existing behavior
- [x] Reuse the mock setup pattern from `test_happy_path_success` (Service, ClaudeExecutor, Logger, Runner, is_git_repo, load_config, detect_local_dir, check_claude_dep, _install_sigquit, TerminalCollector all patched)
- [x] run `pdm run pytest tests/test_cli.py::TestRunReviewMode -v` — must pass
- [x] run `pdm run ruff check src/ tests/` — must pass
- [x] run `pdm run mypy src/` — must pass

### Task 4: Verify acceptance criteria

- [ ] run `pdm run pytest` — full suite passes
- [ ] run `pdm run ruff check src/ tests/` — no lint errors
- [ ] run `pdm run mypy src/` — strict type check passes
- [ ] confirm new tests cover: parse, validation rejects (plan/task/no-mode), priority over config, priority over autodetect, regression no-flag path
- [ ] do NOT run `rlx --version` or `rlx --review --help` manually (active session); rely on tests + the unchanged option declaration to confirm typer registration

### Task 5: Update documentation and finalize

- [ ] Update `CLAUDE.md` `cli.py` summary line to mention the `--base` flag for review mode (one-word addition to the existing description; do not expand into prose)
- [ ] Update `README.md` only if it documents review-mode flags; if it does not mention `--review`, skip
- [ ] Move this plan to `tasks/0005-review-flags/completed/` (mirrors prior plan layout `tasks/0004-v03/completed/`)
