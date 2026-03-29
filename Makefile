.DEFAULT_GOAL := help

.PHONY: help install test test-cov lint format format-check typecheck security check docs-serve docs-build build clean release

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install: ## Install dev dependencies
	uv sync --extra dev

test: ## Run unit tests
	uv run pytest

test-cov: ## Run tests with coverage report
	uv run pytest --cov=flaude --cov-report=html --cov-fail-under=90
	@echo "Coverage report: htmlcov/index.html"

lint: ## Run linter (ruff check)
	uv run ruff check .

format: ## Format code (ruff format)
	uv run ruff format .

format-check: ## Check formatting without changes
	uv run ruff format --check .

typecheck: ## Run type checker (mypy)
	uv run mypy flaude/ tests/

security: ## Run security scanner (bandit)
	uv run bandit -r flaude/ -ll

check: lint format-check typecheck security ## Run all quality checks

docs-serve: ## Serve docs locally at localhost:8000
	uv run --extra docs mkdocs serve --dev-addr localhost:8000

docs-build: ## Build docs (strict mode)
	uv run --extra docs mkdocs build --strict

build: ## Build distribution packages (wheel + sdist)
	uv build

clean: ## Remove build artifacts
	rm -rf dist/ site/ .coverage htmlcov/ .ruff_cache/ .mypy_cache/ *.egg-info/

release: ## Create a new release (interactive walkthrough)
	@bash scripts/release.sh
