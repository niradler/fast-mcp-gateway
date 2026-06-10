.DEFAULT_GOAL := help
.PHONY: help install dev lint format format-check typecheck slop test test-cov check build publish run clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Create the venv and install all (incl. dev + cli) dependencies
	uv sync --extra cli

dev: install ## Alias for install (set up the dev environment)

lint: ## Lint with ruff (no changes)
	uv run ruff check .

format: ## Auto-format and apply safe lint fixes
	uv run ruff format .
	uv run ruff check --fix .

format-check: ## Verify formatting without writing (CI)
	uv run ruff format --check .

typecheck: ## Type-check with mypy (strict)
	uv run mypy

slop: ## Flag AI-slop ruff can't see (inline comments, TYPE_CHECKING blocks, long docstrings)
	uv run python tools/check_slop.py src

test: ## Run the test suite
	uv run pytest

test-cov: ## Run tests with a coverage report
	uv run pytest --cov --cov-report=term-missing

check: lint format-check typecheck slop test ## Run the full CI gate (lint + format + types + slop + tests)

build: ## Build sdist and wheel into dist/
	uv build

publish: build ## Publish to PyPI (needs UV_PUBLISH_TOKEN)
	uv publish

run: ## Run the example gateway app with reload
	uv run uvicorn examples.basic_app:app --reload

clean: ## Remove build artifacts and caches
	uv run python -c "import shutil, pathlib; [shutil.rmtree(p, ignore_errors=True) for p in ['dist', 'build', '.pytest_cache', '.mypy_cache', '.ruff_cache', 'htmlcov']]; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]; pathlib.Path('.coverage').unlink(missing_ok=True)"
