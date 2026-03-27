.PHONY: help install install-dev sync test test-unit test-e2e test-cov lint format format-check type-check check clean run server pre-commit-install pre-commit-run

help:
	@echo "Available targets:"
	@echo "  make install          - Install project dependencies"
	@echo "  make install-dev      - Install project with dev dependencies"
	@echo "  make sync             - Sync dependencies (uv sync --group dev)"
	@echo "  make test             - Run all unit tests (excluding e2e)"
	@echo "  make test-unit        - Run unit tests only"
	@echo "  make test-e2e         - Run e2e tests (requires live Gmail)"
	@echo "  make test-cov         - Run tests with coverage report"
	@echo "  make lint             - Run ruff linter"
	@echo "  make format           - Format code with ruff"
	@echo "  make format-check     - Check formatting without fixing"
	@echo "  make type-check       - Run mypy type checker"
	@echo "  make check            - Run all quality checks (lint + format + type-check)"
	@echo "  make clean            - Clean build artifacts and cache"
	@echo "  make run              - Run the MCP server"
	@echo "  make server           - Alias for 'run'"
	@echo "  make pre-commit-install - Install pre-commit hooks"
	@echo "  make pre-commit-run   - Run pre-commit on all files"

# Installation
install:
	uv sync

install-dev:
	uv sync --group dev --group test

sync:
	uv sync --group dev --group test

# Testing
test:
	uv run pytest tests/ -m "not e2e" -v

test-unit:
	uv run pytest tests/ -m "not e2e" -v

test-e2e:
	GMAIL_E2E=1 uv run pytest tests/e2e/ -v -m e2e

test-cov:
	uv run pytest tests/ -m "not e2e" --cov=src/gmail_blade_mcp --cov-report=term-missing -v

# Code quality
lint:
	uv run ruff check src/ tests/

format:
	uv run ruff format src/ tests/

format-check:
	uv run ruff format --check src/ tests/

type-check:
	uv run mypy src/gmail_blade_mcp

check: lint format-check type-check
	@echo "All quality checks passed!"

# Pre-commit
pre-commit-install:
	uv run pre-commit install

pre-commit-run:
	uv run pre-commit run --all-files

# Running
run:
	uv run gmail-blade-mcp

server: run

# Cleanup
clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov dist build *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	@echo "Cleanup complete!"
