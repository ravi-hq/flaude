# Contributing to flaude

Thank you for your interest in contributing!

## Development setup

**Prerequisites**: Python 3.11+, [uv](https://docs.astral.sh/uv/), a Fly.io account (for E2E tests only).

```bash
git clone https://github.com/YOUR_USERNAME/flaude.git
cd flaude
uv sync --extra dev
```

## Running tests

```bash
# Unit tests (no external dependencies)
uv run pytest

# With coverage
uv run pytest --cov=flaude

# E2E tests (requires FLY_API_TOKEN and CLAUDE_CODE_OAUTH_TOKEN in .env)
cp .env.example .env   # fill in your credentials
uv run pytest -m e2e
```

## Linting and type checking

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy flaude
```

## Submitting a pull request

1. Fork the repo and create a branch from `main`.
2. Make your changes with tests for any new behaviour.
3. Run the linter and tests locally — CI must pass.
4. Open a PR against `main` with a clear description of what and why.
5. A maintainer will review and merge.

## Reporting bugs

Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.yml).

## Requesting features

Use the [feature request template](.github/ISSUE_TEMPLATE/feature_request.yml).

## Code style

- Black-compatible formatting enforced by `ruff format`.
- Type annotations required on all public functions.
- Async-first: use `httpx.AsyncClient` and `asyncio`; avoid blocking calls.

## License

By contributing you agree that your contributions will be licensed under the [MIT License](LICENSE).
