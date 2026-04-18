# Testing and Linting

## Runtime Environment

All commands MUST be run from the current project venv. Do NOT use `pdm run` — invoke tools directly (e.g., `pytest`, `ruff`, `mypy`).

If the venv is not activated, activate it first:

```bash
source venv/bin/activate
```

## Ruff Configuration

- **Line length**: 120 characters
- **Quote style**: Double quotes (`"`)
- **Target**: Python 3.14
- **Rules**: `select = ["ALL"]` (all rules enabled, disable specific ones as needed)

```bash
ruff check src/ tests/          # Lint
ruff check src/ tests/ --fix    # Lint + auto-fix
ruff format src/ tests/         # Format
```

## Mypy

Strict mode for type checking:

```bash
mypy src/
```

Configuration in `pyproject.toml`:
- `strict = true`
- `python_version = "3.14"`

## Pytest

**Dependencies**: pytest, pytest-cov

### Running Tests

```bash
pytest                         # All tests
pytest tests/test_config.py    # Specific file
pytest --cov=src/rlx           # With coverage
pytest -k "test_signal" -v     # Specific tests
```

### Testing Patterns

1. **Protocol-based mocking**: All major interfaces (Executor, Logger, InputCollector, GitChecker) are Protocols — mock them with simple classes or unittest.mock
2. **CommandRunner for executor tests**: ClaudeExecutor accepts CommandRunner protocol — inject mock instead of running real `claude` CLI
3. **stdin/stdout mocking**: TerminalCollector tests mock sys.stdin/sys.stdout
4. **tmp_path fixture**: For config file tests, progress file tests
5. **Parametrized tests**: `@pytest.mark.parametrize` for signal detection, pattern matching, config merge

### Test Structure

| File | Coverage |
|------|----------|
| `test_status.py` | Phase, Signal, Section types and helpers |
| `test_config.py` | Defaults, TOML merge, duration parsing, color parsing, validation |
| `test_input.py` | ask_question, ask_draft_review, ask_yes_no with mock stdin |
| `test_executor.py` | ClaudeExecutor with mock CommandRunner, stream parsing, signal detection, pattern matching, idle timeout |
| `test_processor.py` | Runner.run_plan_creation() with mock Executor/Logger/Input |
| `test_progress.py` | Logger output format, file creation, timestamps, colors |
| `test_signals.py` | parse_question_payload, parse_plan_draft_payload |
| `test_cli.py` | typer app, mode determination, version output |

## Code Style Conventions

- Type annotations everywhere (mypy strict)
- Sync-first: no `async def` — use `subprocess.Popen` and `threading`
- Protocols (typing.Protocol) for all interfaces — enables easy testing
- Dataclasses for data types (Config, Result, Section, etc.)
- match/case for signal dispatching and mode determination
- No classes where a function suffices
- Platform code: `sys.platform` checks, separate `_unix.py`/`_windows.py` modules

## Pre-commit Checklist

Always run before committing:

```bash
ruff check src/ tests/
ruff format src/ tests/
mypy src/
pytest
```

## Validation Commands Summary

```bash
pytest                  # Tests pass
ruff check src/ tests/  # No lint errors
ruff format src/ tests/ # Formatting
mypy src/               # Type checking (strict)
```
