# Fix: Show real-time progress during rlx --plan

## Overview

When rlx --plan runs, Claude explores the codebase via tool calls for several minutes before producing text output. During this time the user sees nothing and believes the process is hung. Fix by adding tool activity display to the terminal and setting a default idle timeout as a safety net.

## Context

- Files involved: src/rlx/executor/claude_executor.py, src/rlx/cli.py, src/rlx/config.py, src/rlx/progress/logger.py
- Root cause: ClaudeExecutor created without output_handler (parameter exists but not passed); idle_timeout defaults to "0" (disabled); _extract_text() only processes text events, not tool_use events
- The 8-minute "hang" was actually Claude working (tool calls to explore codebase), not a real hang

## Development Approach

- **Testing approach**: Regular (code first, then tests)
- Complete each task fully before moving to the next
- **CRITICAL: every task MUST include new/updated tests**
- **CRITICAL: all tests must pass before starting next task**

## Implementation Steps

### Task 1: Add tool activity callback to ClaudeExecutor

**Files:**
- Modify: `src/rlx/executor/claude_executor.py`
- Modify: `tests/test_executor.py`

- [x] Add `activity_handler: Callable[[str], None] | None = None` parameter to `ClaudeExecutor.__init__`, store as `self._activity_handler`
- [x] In `run()` method's JSON event loop, after parsing event as dict, detect `content_block_start` events where `content_block.type == "tool_use"` -- extract `content_block.name` (the tool name)
- [x] When tool_use detected and `self._activity_handler` is set, call `self._activity_handler(tool_name)`
- [x] Write tests in `tests/test_executor.py`:
  - `activity_handler` called with correct tool name for `tool_use` `content_block_start` event
  - `activity_handler` NOT called for text-only events
  - `activity_handler=None` (default) does not crash on tool_use events
  - Multiple tool_use events call handler multiple times with different names
- [x] Run `pdm run pytest tests/test_executor.py -v` -- must pass
- [x] Run `pdm run ruff check src/rlx/executor/ tests/test_executor.py` -- no errors
- [x] Run `pdm run mypy src/` -- passes

### Task 2: Add file-only logging method to Logger, wire up handlers in CLI

**Files:**
- Modify: `src/rlx/progress/logger.py`
- Modify: `src/rlx/cli.py`
- Modify: `tests/test_progress.py`
- Modify: `tests/test_cli.py`

- [x] Add `log_claude_output(self, text: str) -> None` method to `Logger` -- writes text to progress file only (not stdout), used for capturing Claude's raw streaming text for debugging
- [x] In `cli.py` `run_plan_mode()`, create `activity_handler` callback that calls `log.print("claude: %s", tool_name)` -- shows timestamped tool activity on terminal and writes to progress file
- [x] In `cli.py` `run_plan_mode()`, create `output_handler` callback that calls `log.log_claude_output(text)` -- writes Claude's text to progress file only (no terminal output to avoid showing raw signal markup)
- [x] Pass both `activity_handler` and `output_handler` to `ClaudeExecutor` constructor in `run_plan_mode()`
- [x] Write tests:
  - `Logger.log_claude_output` writes to file, does not write to stdout
  - CLI `run_plan_mode` creates executor with `output_handler` and `activity_handler` (verify via mock executor)
- [x] Run `pdm run pytest tests/ -v` -- must pass
- [x] Run `pdm run ruff check src/ tests/` -- no errors
- [x] Run `pdm run mypy src/` -- passes

### Task 3: Set reasonable default idle_timeout

**Files:**
- Modify: `src/rlx/config.py`
- Modify: `tests/test_config.py`

- [x] Change default `idle_timeout` from `"0"` to `"5m"` in `Config` dataclass
- [x] Update any tests that assert `idle_timeout` default is `"0"` to expect `"5m"`
- [x] Run `pdm run pytest tests/ -v` -- must pass
- [x] Run `pdm run ruff check src/ tests/` -- no errors
- [x] Run `pdm run mypy src/` -- passes

### Task 4: Verify acceptance criteria

- [x] Run `pdm run pytest tests/ -v` -- full test suite passes
- [x] Run `pdm run ruff check src/ tests/` -- no linter errors
- [x] Run `pdm run mypy src/` -- strict type checking passes
- [x] Verify `pdm run rlx --version` outputs the version string

### Task 5: Update documentation

- [x] Update `CLAUDE.md` if internal patterns changed (activity_handler parameter)
- [x] Move this plan to `docs/plans/completed/`
