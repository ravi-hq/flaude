# Contributing to flaude

Thank you for your interest in contributing!

## Development setup

**Prerequisites**: Python 3.11+, [uv](https://docs.astral.sh/uv/), a Fly.io account (for E2E tests only).

```bash
git clone https://github.com/ravi-hq/flaude.git
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

## Releasing a new version

> Maintainers only.

### Via Claude Code

Run `/release` — it walks through version bump, CHANGELOG update, commit, tag, and GitHub Release creation interactively.

### Via terminal

```bash
make release
```

This runs `scripts/release.sh` which:
1. Checks preconditions (clean tree, on main, up to date)
2. Prompts for the new version number
3. Updates `pyproject.toml` and opens `CHANGELOG.md` in `$EDITOR`
4. Commits, tags, pushes, and creates a GitHub Release

The GitHub Release triggers CI to:
- Publish to [PyPI](https://pypi.org/project/flaude/) via OIDC trusted publishing
- Build and push Docker image to `ghcr.io/ravi-hq/flaude`

### Version policy

- Versions follow [Semantic Versioning](https://semver.org/)
- Version source of truth: `version` field in `pyproject.toml`
- CHANGELOG follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)

## Code style

- Black-compatible formatting enforced by `ruff format`.
- Type annotations required on all public functions.
- Async-first: use `httpx.AsyncClient` and `asyncio`; avoid blocking calls.

## License

By contributing you agree that your contributions will be licensed under the [MIT License](LICENSE).
