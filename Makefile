.PHONY: install test test-cov lint lint-fix format typecheck check

install:
	pdm install --dev

test:
	pytest tests/ -v

test-cov:
	pytest tests/ --cov=src/rlx --cov-report=term-missing

lint:
	ruff check src/ tests/

lint-fix:
	ruff check src/ tests/ --fix

format:
	ruff format src/ tests/

typecheck:
	mypy src/

check: lint typecheck test
