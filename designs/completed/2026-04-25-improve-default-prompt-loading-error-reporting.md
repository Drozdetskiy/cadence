# Improve default prompt loading error reporting

## Overview

The `rlx --review` phase failed at runtime with an opaque `[Errno 2] No such file or directory: '.../site-packages/rlx/defaults/prompts/review_first.txt'`. Root cause: `load_prompt()` in `src/rlx/processor/prompts.py` lets the bare `FileNotFoundError` from `importlib.resources` bubble up. The user gets no hint that this is an embedded prompt missing from the rlx install (typical cause: a stale non-editable install whose data files are out of date relative to the source tree). On top of that, the test suite never asserts that every embedded default prompt and agent is actually present in the package, so a packaging regression would not be caught in CI.

This plan: wrap the embedded-resource read in `load_prompt()` (and the analogous one in `agents.py`) with a clear diagnostic, and add regression tests that load every shipped default.

## Context

- Files involved:
  - `src/rlx/processor/prompts.py` (`load_prompt`)
  - `src/rlx/processor/agents.py` (embedded agent loader)
  - `tests/test_prompts.py` (extend)
  - `tests/test_agents.py` (extend or create — TBD by reading existing file)
- Related patterns:
  - All embedded resources are accessed via `importlib.resources.files("rlx.defaults.<x>")`.
  - Existing `TestLoadTaskPrompt` already asserts `load_prompt("task")` returns expected content; pattern to follow.
  - Local override + embedded fallback split is already established in both `prompts.py` and `agents.py`.
- Dependencies: none new. Uses stdlib `importlib.resources` only.

## Development Approach

- **Testing approach**: Regular (code first, then tests), since the change is small and the assertion shape is straightforward.
- Complete each task fully before moving to the next.
- **CRITICAL: every task MUST include new/updated tests**
- **CRITICAL: all tests must pass before starting next task**

## Implementation Steps

### Task 1: Wrap embedded prompt read with diagnostic error

**Files:**
- Modify: `src/rlx/processor/prompts.py`
- Modify: `tests/test_prompts.py`

- [x] In `load_prompt`, wrap the `importlib.resources` read in `try/except FileNotFoundError`
- [x] On miss, raise `RuntimeError` with a message naming the prompt (e.g. `"default prompt 'review_first' not found in installed rlx package; the install may be incomplete or out of date — reinstall with 'pip install -e .'"`)
- [x] Keep the original exception via `raise ... from exc`
- [x] Add tests in `tests/test_prompts.py` covering: (a) `load_prompt("task")` still returns content, (b) `load_prompt("does_not_exist")` raises `RuntimeError` whose message contains the prompt name and the word "rlx"
- [x] run `pytest tests/test_prompts.py -v` — must pass before Task 2

### Task 2: Same diagnostic for embedded agent loader

**Files:**
- Modify: `src/rlx/processor/agents.py`
- Modify or create: `tests/test_agents.py`

- [x] Read `agents.py` to find the `importlib.resources` access path for embedded agent bodies
- [x] Apply the same `try/except` wrap with a clear `RuntimeError` ("default agent '<name>' not found in installed rlx package …" style)
- [x] Add a test verifying the diagnostic fires for an unknown embedded agent (use a name guaranteed not to exist among `quality/implementation/testing/simplification/documentation`)
- [x] run `pytest tests/test_agents.py -v` (or wherever the new test lives) — must pass before Task 3

### Task 3: Regression coverage for all shipped defaults

**Files:**
- Modify: `tests/test_prompts.py`
- Modify: `tests/test_agents.py`

- [x] Add a parametrised (or simple multi-name) test asserting `load_prompt(name)` succeeds and returns non-empty content for each of: `make_plan`, `task`, `review_first`, `review_second`, `finalize`
- [x] Add a similar test for embedded agents: `quality`, `implementation`, `testing`, `simplification`, `documentation` — assert each loads and returns non-empty content
- [x] These tests guard against accidental file removal or build-config regression dropping data files
- [x] run `pytest tests/ -v` — must pass before Task 4

### Task 4: Verify acceptance criteria

- [x] run `make check` (lint + mypy + tests)
- [x] manually load `build_review_first_prompt(plan_file="", default_branch="main")` to confirm normal path still works end-to-end
- [x] confirm a deliberately-broken call (e.g. `load_prompt("missing")`) now produces the new diagnostic, not the bare `[Errno 2]`

### Task 5: Update documentation and finalize

- [x] no README changes expected (no user-facing CLI surface change)
- [x] no CLAUDE.md changes expected (the `prompts.py` one-line summary still describes its purpose accurately)
- [x] move this plan to `designs/completed/`
