# Remove documentation review agent (4 agents remain)

## Overview

Remove the `documentation` review agent from rlx. After this change, `review_first.txt` launches 4 agents in parallel (quality, implementation, testing, simplification) instead of 5. `review_second.txt` is unaffected (it already only uses quality + implementation). The embedded `documentation.txt` agent body and all references in code, tests, and docs are deleted.

## Context

- Files involved:
  - `src/rlx/defaults/prompts/review_first.txt` — contains `{{agent:documentation}}` marker and "ALL 5 Review Agents" wording
  - `src/rlx/defaults/agents/documentation.txt` — embedded agent body to delete
  - `tests/test_agents.py` — `test_documentation_body_loads` and `test_all_shipped_agents_load` parametrize list
  - `tests/test_prompts.py` — `TestBuildReviewFirstPrompt.test_expands_all_five_agents_and_substitutes` asserts count=5 and iterates 5 agent names
  - `CLAUDE.md` — lists `documentation.txt` in `defaults/agents/` description
  - `docs/reference/09-agents.md` — full section + summary tables
  - `docs/reference/08-prompts.md` — `{{agent:documentation}}` reference, prompt excerpt, and PhaseReview table row
  - `docs/reference/10-features.md` — agent list bullet
  - `docs/reference/02-config.md` — agent files tree
- Related patterns:
  - Agent loading via `load_agent` in `src/rlx/processor/agents.py` (no code changes — loader is generic)
  - Prompt expansion via `expand_agent_references` in `src/rlx/processor/prompts.py` (no code changes)
- Dependencies: none

## Development Approach

- Testing approach: Regular (code first, then update tests)
- Complete each task fully before moving to the next
- Per docs/reference/09-agents.md "Отключение агента": removing the local file does NOT disable an agent — the embedded default is still loaded. To fully remove, both the embedded `documentation.txt` and every `{{agent:documentation}}` marker must be deleted.
- CRITICAL: every task MUST include new/updated tests
- CRITICAL: all tests must pass before starting next task

## Implementation Steps

### Task 1: Remove documentation agent from review_first prompt and delete embedded body

**Files:**
- Modify: `src/rlx/defaults/prompts/review_first.txt`
- Delete: `src/rlx/defaults/agents/documentation.txt`

- [x] In `review_first.txt`, change "Step 2: Launch ALL 5 Review Agents IN PARALLEL" to "Step 2: Launch ALL 4 Review Agents IN PARALLEL"
- [x] In `review_first.txt`, change "Do NOT proceed to Step 3 until ALL 5 agents have returned results." to "Do NOT proceed to Step 3 until ALL 4 agents have returned results."
- [x] In `review_first.txt`, remove the line `{{agent:documentation}}` from the "Agents to launch:" block
- [x] Delete `src/rlx/defaults/agents/documentation.txt`
- [x] Update `tests/test_agents.py`: remove `test_documentation_body_loads` and remove `"documentation"` from the `test_all_shipped_agents_load` parametrize list
- [x] Update `tests/test_prompts.py`: in `TestBuildReviewFirstPrompt`, rename `test_expands_all_five_agents_and_substitutes` to `test_expands_all_four_agents_and_substitutes`; drop `"documentation"` from the iterated agent names; change the expected count of "Report findings only - no positive observations." from 5 to 4; add an assertion that `{{agent:documentation}}` does not appear in the output AND that the documentation agent body sentinel ("Review code changes and identify missing documentation updates.") does NOT appear in the result
- [x] Run `pytest tests/ -v` — must pass before task 2
- [x] Run `ruff check src/ tests/` and `mypy src/` — must pass before task 2

### Task 2: Update CLAUDE.md and reference docs

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/reference/09-agents.md`
- Modify: `docs/reference/08-prompts.md`
- Modify: `docs/reference/10-features.md`
- Modify: `docs/reference/02-config.md`

- [x] In `CLAUDE.md`, remove `, documentation.txt` from the `defaults/agents/` line so the list reads `quality.txt, implementation.txt, testing.txt, simplification.txt`
- [x] In `docs/reference/09-agents.md`: delete the entire `## documentation.txt -- Агент документации` section (and its trailing `---`); remove the `documentation` row from both summary tables (the "Где используются агенты" table and the "Сводная таблица агентов" table at the end); update "Первое ревью: 5 агентов параллельно" to "Первое ревью: 4 агента параллельно"
- [x] In `docs/reference/08-prompts.md`: remove the `{{agent:documentation}}` bullet from the agent list; remove the `{{agent:documentation}}` line from the prompt excerpt; in the PhaseReview (1st) table row change `5 (quality, implementation, testing, simplification, documentation)` to `4 (quality, implementation, testing, simplification)`
- [x] In `docs/reference/10-features.md`: remove the `- ``documentation.txt`` -- необходимость обновления документации` bullet
- [x] In `docs/reference/02-config.md`: remove the `documentation.txt` line from the agents directory tree
- [x] No code tests needed for this task — docs only. Re-run `pytest tests/ -v` to confirm Task 1 still passes.

### Task 3: Verify acceptance criteria

- [ ] Run full test suite: `pytest tests/ -v`
- [ ] Run linter: `ruff check src/ tests/`
- [ ] Run type checker: `mypy src/`
- [ ] Run `make check` to confirm everything is green
- [ ] Grep the repo for any remaining references: `documentation\.txt`, `agent:documentation`, "documentation review", "5 (quality" — only `tasks/0004-v03/...` historical plan files (which are completed historical records, do not modify) and `tasks/0006-no-docs-step/preprompt` should match
- [ ] Verify `rlx --version` still works

### Task 4: Update plan tracking

- [ ] Move this plan to `docs/plans/completed/` with a date prefix (e.g. `2026-04-30-remove-documentation-review-agent.md`)
