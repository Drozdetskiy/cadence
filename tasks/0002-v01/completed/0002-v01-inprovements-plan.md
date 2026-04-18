# Remove Draft Review Prompt and Add --impl Flag

## Overview
Three changes to the plan creation flow:
1. Remove the 4-option draft review menu (Accept/Revise/Interactive review/Reject) - drafts auto-accept and proceed to next iteration without user interaction
2. Remove the "Continue with implementation?" question after plan completion
3. Add --impl flag to rlx --plan that stores the intent for auto-implementation after plan creation (actual implementation logic is out of scope)
4. Show "run: rlx --task <path-to-plan>" hint in the final success message

## Context
- Files involved:
  - src/rlx/processor/runner.py - remove draft review logic, auto-accept drafts
  - src/rlx/cli.py - add --impl flag, remove ask_yes_no call, update success message
  - src/rlx/defaults/prompts/make_plan.txt - remove Step 3.5 draft review instructions
  - src/rlx/input.py - no changes needed (ask_draft_review stays for potential future use, just won't be called)
  - tests/test_processor.py - update draft flow tests
  - tests/test_cli.py - add --impl flag tests, update existing tests
- Related patterns: existing typer.Option pattern for --plan/--task/--review flags
- Dependencies: none

## Development Approach
- **Testing approach**: Regular (code first, then tests)
- Complete each task fully before moving to the next
- **CRITICAL: every task MUST include new/updated tests**
- **CRITICAL: all tests must pass before starting next task**

## Implementation Steps

### Task 1: Remove draft review from runner and auto-accept drafts

**Files:**
- Modify: `src/rlx/processor/runner.py`

- [x] In run_plan_creation(), when a draft is parsed via parse_plan_draft_payload(), skip calling _handle_plan_draft() entirely. Instead, log the auto-accept and set last_revision_feedback = "" (behave as if user accepted). The draft content is already written to the progress file by Claude's output, so the next iteration will see "DRAFT REVIEW: accept" equivalent behavior
- [x] Remove the _handle_plan_draft method (or keep it but it won't be called - prefer removal)
- [x] Remove InputCollector from Runner's required dependencies for plan mode (set_input_collector can remain but won't be needed for plan flow)
- [x] Update tests in tests/test_processor.py: remove/update TestRunnerPlanCreationDraftFlow tests that test accept/revise/reject flows. Add a test that verifies drafts are auto-accepted without calling ask_draft_review
- [x] Run project test suite - must pass before task 2

### Task 2: Update prompt to remove draft review instructions

**Files:**
- Modify: `src/rlx/defaults/prompts/make_plan.txt`

- [x] Remove Step 3.5 (Present Draft for Review) entirely - Claude should not emit PLAN_DRAFT signal at all
- [x] Remove handling of user responses section (accept/revise/reject)
- [x] Update Step 4 to no longer depend on draft acceptance - Claude should write the plan file directly when it has enough information and validation passes
- [x] Remove PLAN_DRAFT from the signal flow description; the flow becomes: questions -> write plan file -> emit PLAN_READY
- [x] Update Step 4.5 validation to emit PLAN_READY directly after writing plan file (no intermediate draft step)
- [x] Run project test suite - must pass before task 3

### Task 3: Add --impl flag and update success message in CLI

**Files:**
- Modify: `src/rlx/cli.py`

- [x] Add --impl flag as typer.Option(False, "--impl", help="Auto-implement after plan creation"). It should only be valid together with --plan (error if used with --task or --review or alone)
- [x] Remove the ask_yes_no("Continue with implementation?") call and the "(implementation not available in v0.1)" message after successful plan creation
- [x] After successful plan creation, print a message showing how to run implementation: "run: rlx --task <path-to-plan>" where path-to-plan is the path to the created plan file (this needs to be derived from the plan_file input - the plan is written next to the prompt file by Claude)
- [x] When --impl is passed, log that auto-implementation will follow (but don't implement the actual execution - just store the flag and log the intent). For now, print "(implementation not available in v0.1)" only when --impl is set
- [x] Pass impl flag through to run_plan_mode (add impl parameter)
- [x] Write tests: test --impl flag parsing, test --impl requires --plan, test success message contains "rlx --task", test --impl without --plan errors
- [x] Run project test suite - must pass before task 4

### Task 4: Clean up runner Protocol and unused imports

**Files:**
- Modify: `src/rlx/processor/runner.py`
- Modify: `src/rlx/input.py` (if needed)

- [x] Remove ask_draft_review from InputCollector Protocol in runner.py (plan mode no longer needs it)
- [x] Remove log_draft_review from Logger Protocol in runner.py (no longer called)
- [x] Remove UserRejectedPlanError if no longer raised (check all usages first - cli.py still catches it, so keep it but it won't be raised from runner)
- [x] Clean up any unused imports in runner.py (ACTION_ACCEPT, ACTION_REJECT, ACTION_REVISE if imported)
- [x] Update tests to reflect protocol changes
- [x] Run project test suite - must pass before task 5

### Task 5: Verify acceptance criteria

- [x] Run full test suite: pytest tests/ -v
- [x] Run linter: ruff check src/ tests/
- [x] Run type checker: mypy src/
- [x] Verify: rlx --plan <file> no longer shows draft review menu
- [x] Verify: rlx --plan <file> no longer asks "Continue with implementation?"
- [x] Verify: rlx --plan <file> --impl is accepted as valid
- [x] Verify: rlx --impl alone shows error
- [x] Verify: success message shows "rlx --task <path>"

### Task 6: Update documentation

- [x] Update CLAUDE.md if internal patterns changed (e.g., signal flow description)
- [x] Move this plan to docs/plans/completed/
