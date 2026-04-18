# Add timestamps to all Logger output methods

## Overview

Add timestamps in `[YY-MM-DD HH:MM:SS]` format to all Logger output methods that currently lack them: `print_section()`, `print_raw()`, and `log_claude_output()`. Currently only `print()`, `error()`, and `warn()` include timestamps.

## Context

- Files involved: `src/rlx/progress/logger.py`, `tests/test_progress.py`
- Related patterns: existing `_timestamp()` method produces `[%y-%m-%d %H:%M:%S]` format, used by `print()`, `error()`, `warn()`
- Dependencies: none

## Development Approach

- **Testing approach**: Regular (code first, then tests)
- Complete each task fully before moving to the next
- **CRITICAL: every task MUST include new/updated tests**
- **CRITICAL: all tests must pass before starting next task**

## Implementation Steps

### Task 1: Add timestamps to print_section and print_raw

**Files:**
- Modify: `src/rlx/progress/logger.py`
- Modify: `tests/test_progress.py`

- [x] In `print_section()`, change file output from `\n--- {label} ---\n` to `\n{ts} --- {label} ---\n` where ts comes from `_timestamp()`
- [x] In `print_section()`, add timestamp styling to console output using `_colors.timestamp()` (matching `print()` pattern)
- [x] In `print_raw()`, prepend timestamp to both file and console output (matching `print()` pattern)
- [x] Update `test_section_writes` to assert timestamp bracket pattern `[` is present in section output
- [x] Add test for `print_raw` verifying timestamp is included
- [x] Run project test suite - must pass before task 2

### Task 2: Add timestamps to log_claude_output at line boundaries

**Files:**
- Modify: `src/rlx/progress/logger.py`
- Modify: `tests/test_progress.py`

- [x] Add `_output_at_line_start: bool = True` field to Logger `__init__`
- [x] In `log_claude_output()`, for file output: always prepend `_timestamp()` to each `_write_to_file()` call
- [x] In `log_claude_output()`, for console output: prepend timestamp only when `_output_at_line_start` is True and text has content; update `_output_at_line_start` based on whether text ends with `\n`
- [x] Update `test_log_claude_output_writes_to_file_and_stdout` to verify timestamp is present in file output
- [x] Add test: consecutive `log_claude_output` calls without newline produce only one timestamp on console
- [x] Add test: `log_claude_output` with `\n` at end resets line state, next call gets new timestamp
- [x] Run project test suite - must pass before task 3

### Task 3: Verify acceptance criteria

- [x] Run full test suite: `pytest tests/ -v`
- [x] Run linter: `ruff check src/ tests/`
- [x] Run type checker: `mypy src/`
