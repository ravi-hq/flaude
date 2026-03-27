# OSS Requirements Implementation Plan

## Overview

Bring flaude up to modern open source project standards: CI, code quality enforcement, complete PyPI metadata, community health files, release automation, and DX polish. The project has strong fundamentals (372 tests, 94% coverage, clean mypy on source) but is not ready for public OSS distribution or community contribution.

## Research Summary

Research conducted by agent team with 5 specialist tracks:
- **CI/CD**: Only `docs.yml` exists. No test, lint, publish, or Docker workflows.
- **Quality tooling**: ruff/mypy/coverage not configured in `pyproject.toml`; not in dev deps; 24 files need formatting; 3 lint issues; 34 mypy errors in tests.
- **Community health**: Missing CODE_OF_CONDUCT, SECURITY, issue templates, PR template, CHANGELOG, `.env.example`. CONTRIBUTING.md has 3 broken references.
- **Packaging**: Missing `readme`, `authors`, `license`, `keywords`, `classifiers`, `[project.urls]` — PyPI page would be blank today.
- **DX**: No badges, no Makefile, README dev section inconsistent with CONTRIBUTING (pip vs uv), `mkdocs.yml` missing `site_url`/`repo_url`.

### Key Discoveries:
- `pyproject.toml:5-12` — `[project]` has only `name`, `version`, `description`, `requires-python`, `dependencies`
- `pyproject.toml:15-18` — `[dev]` extras: only `pytest`, `pytest-asyncio`, `respx`; ruff/mypy/coverage absent
- `CONTRIBUTING.md:10` — placeholder `YOUR_USERNAME` clone URL
- `CONTRIBUTING.md:25` — references `.env.example` that doesn't exist
- `CONTRIBUTING.md:47,51` — links to `.github/ISSUE_TEMPLATE/bug_report.yml` and `feature_request.yml` that don't exist
- `flaude/fly_client.py` — 1 unused import (ruff F401)
- `tests/test_e2e.py` — ambiguous variable name `l` (ruff E741)
- `tests/test_concurrent_integration.py` — 1 unused import (ruff F401)
- `tests/` — 34 mypy errors (type mismatches, untyped test functions), mostly `test_exit_code_propagation.py`

## Design Decisions

- **PyPI publishing**: OIDC trusted publishing (no API token needed — configure on PyPI side once)
- **Docker registry**: `ghcr.io/ravi-hq/flaude` using `GITHUB_TOKEN` (no extra secrets)
- **Versioning**: Simple manual tags via `make release` guided script
- **mypy scope**: Full — `flaude/` + `tests/` (fix all 34 errors in Phase 2)
- **Docs URL**: `https://ravi-hq.github.io/flaude`
- **ruff target**: `["E", "F", "I", "W", "UP"]` — errors, pyflakes, isort, warnings, pyupgrade

## Current State Analysis

```
pyproject.toml    — functional for local builds; not PyPI-ready
.github/          — only docs.yml workflow
flaude/           — well-typed, clean source; missing py.typed marker
tests/            — 372 passing; 24 files unformatted; 34 mypy errors
CONTRIBUTING.md   — substantive but 3 broken references
README.md         — good content; no badges, no docs link
mkdocs.yml        — deploys to GH Pages; missing site_url, repo_url
.gitignore        — minimal; missing __pycache__/, *.egg-info/, build/
```

## Desired End State

- `uv sync --extra dev` installs everything needed to contribute
- `make check` runs ruff + mypy + bandit and passes clean
- `make test` runs 372 tests; `make test-cov` enforces ≥90% coverage
- Every PR triggers CI (test matrix + lint + type check)
- Push to `main` triggers Docker build → `ghcr.io/ravi-hq/flaude:latest`
- `make release` guides through version bump → tag → push → PyPI publish
- `pip install flaude` shows full PyPI page with description, classifiers, links
- New contributors find CODE_OF_CONDUCT, SECURITY, issue templates, PR template, `.env.example`
- README has badges for CI, PyPI, Python, license, docs

## What We're NOT Doing

- No dynamic versioning (hatch-vcs, commitizen) — simple manual tags
- No semantic-release or auto-generated CHANGELOG from commits
- No devcontainer / GitHub Codespaces support
- No matrix testing across OS (Linux-only CI)
- No `mypy --strict` on tests — fix existing errors, then standard settings
- No tox / nox
- No CODEOWNERS (no co-maintainers yet)

---

## File Ownership Map

| File | Phase | Owner Track | Change Type |
|------|-------|-------------|-------------|
| `pyproject.toml` | 1A | config | modify |
| `CONTRIBUTING.md` | 1B | files | modify |
| `.gitignore` | 1B | files | modify |
| `mkdocs.yml` | 1B | files | modify |
| `.env.example` | 1B | files | create |
| `flaude/py.typed` | 1B | files | create |
| `flaude/*.py` (format) | 2 | quality | modify |
| `tests/*.py` (format + mypy) | 2 | quality | modify |
| `.pre-commit-config.yaml` | 2 | quality | create |
| `.github/workflows/ci.yml` | 3A | ci | create |
| `.github/workflows/docker.yml` | 3A | ci | create |
| `CODE_OF_CONDUCT.md` | 3B | community | create |
| `SECURITY.md` | 3B | community | create |
| `.github/ISSUE_TEMPLATE/bug_report.yml` | 3B | community | create |
| `.github/ISSUE_TEMPLATE/feature_request.yml` | 3B | community | create |
| `.github/PULL_REQUEST_TEMPLATE.md` | 3B | community | create |
| `CHANGELOG.md` | 3B | community | create |
| `Makefile` | 4A | release | create |
| `scripts/release.sh` | 4A | release | create |
| `.github/workflows/release.yml` | 4A | release | create |
| `.github/dependabot.yml` | 4A | release | create |
| `README.md` | 4B | dx | modify |

**Conflict-free guarantee**: No file appears in multiple owner tracks within the same phase.

---

## Phase 1: Foundation

### Overview
Fix all broken references, complete `pyproject.toml` metadata and tool configs, add missing dev dependencies. No code logic changes — purely config and small new files.

### Parallel tracks:
- **Track A (config)**: `pyproject.toml` — all additions
- **Track B (files)**: `CONTRIBUTING.md`, `.gitignore`, `mkdocs.yml`, `.env.example`, `flaude/py.typed`

### Changes Required:

#### 1A. `pyproject.toml` — complete rewrite of `[project]` section and add tool configs

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "flaude"
version = "0.1.0"
description = "Spin up Fly.io machines to execute Claude Code prompts"
readme = "README.md"
license = {file = "LICENSE"}
requires-python = ">=3.11"
authors = [
    {name = "flaude contributors"},
]
keywords = ["fly.io", "claude", "claude-code", "ai", "llm", "automation", "ephemeral"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Software Development :: Libraries :: Python Modules",
    "Topic :: Internet",
    "Typing :: Typed",
]
dependencies = [
    "httpx>=0.27,<1",
]

[project.urls]
Homepage = "https://github.com/ravi-hq/flaude"
Documentation = "https://ravi-hq.github.io/flaude"
Repository = "https://github.com/ravi-hq/flaude"
"Bug Tracker" = "https://github.com/ravi-hq/flaude/issues"
Changelog = "https://github.com/ravi-hq/flaude/blob/main/CHANGELOG.md"

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.24",
    "respx>=0.22",
    "mypy>=1.8",
    "ruff>=0.3",
    "coverage[toml]>=7",
    "bandit[toml]>=1.7",
    "pre-commit>=3",
]
docs = [
    "mkdocs-material>=9.5",
    "mkdocstrings[python]>=0.27",
]

[tool.hatch.build.targets.wheel]
packages = ["flaude"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-m 'not e2e'"
markers = [
    "e2e: real Fly.io integration tests (require FLY_API_TOKEN and CLAUDE_CODE_OAUTH_TOKEN)",
]

[tool.ruff.lint]
select = ["E", "F", "I", "W", "UP"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"

[tool.mypy]
python_version = "3.11"
ignore_missing_imports = true
strict = false
warn_unused_ignores = true
warn_return_any = true
disallow_untyped_defs = true

[tool.coverage.run]
source = ["flaude"]
omit = ["tests/*"]

[tool.coverage.report]
fail_under = 90
show_missing = true

[tool.bandit]
targets = ["flaude"]

[tool.hatch.envs.docs]
features = ["docs"]

[tool.hatch.envs.docs.scripts]
serve = "mkdocs serve --dev-addr localhost:8000"
build = "mkdocs build --strict"
deploy = "mkdocs gh-deploy --force"
```

Note on `[tool.mypy]`: using `disallow_untyped_defs = true` without `strict = true` avoids the stricter `disallow_any_generics` / `disallow_subclassing_any` rules that would hit test files hard. Full `--strict` can be enabled later.

#### 1B. `CONTRIBUTING.md` — fix 3 broken references

Change `pyproject.toml:10`:
```
git clone https://github.com/YOUR_USERNAME/flaude.git
```
→
```
git clone https://github.com/ravi-hq/flaude.git
```

The `.env.example` reference at line 25 will be satisfied by creating that file (below).
The issue template references at lines 47, 51 will be satisfied in Phase 3B.

#### 1B. `.gitignore` — add missing standard Python entries

Append to existing file:
```
__pycache__/
*.egg-info/
build/
htmlcov/
*.pyo
.tox/
```

#### 1B. `mkdocs.yml` — add site_url and repo_url

Add after `site_description`:
```yaml
site_url: https://ravi-hq.github.io/flaude
repo_url: https://github.com/ravi-hq/flaude
repo_name: ravi-hq/flaude
edit_uri: edit/main/docs/
```

#### 1B. `.env.example` — create with all E2E test variables

```bash
# Required for E2E tests (flaude/tests/test_e2e.py)
# Copy to .env: cp .env.example .env

# Fly.io API token — https://fly.io/user/personal_access_tokens
FLY_API_TOKEN=

# Claude Code OAuth token for authenticating Claude Code on Fly machines
CLAUDE_CODE_OAUTH_TOKEN=

# GitHub credentials (only needed if cloning private repos)
GITHUB_USERNAME=
GITHUB_TOKEN=

# Fly.io app name to use for E2E tests
FLY_APP_NAME=flaude-e2e
```

#### 1B. `flaude/py.typed` — create empty PEP 561 marker

Empty file. No content.

### Success Criteria:

- [ ] `uv sync --extra dev` installs ruff, mypy, coverage, bandit, pre-commit
- [ ] `python -c "import flaude"` still works
- [ ] `uv build` produces wheel with `readme`, `classifiers` in metadata: `unzip -p dist/*.whl '*/METADATA' | grep -E 'Classifier|Home-page|Project-URL|Summary'`
- [ ] `mkdocs build --strict` passes
- [ ] `.env.example` exists at project root
- [ ] `flaude/py.typed` exists

---

## Phase 2: Code Quality

### Overview
Sequentially: format all files → fix 3 lint issues → fix 34 mypy errors → add pre-commit config. Must be done in order because ruff format changes lines that mypy errors reference.

### Dependencies
Requires Phase 1 complete (tool configs and dev deps must exist in `pyproject.toml`).

### Sequential steps:

#### Step 1: `ruff format .`
Run once. 24 of 31 files will be reformatted. No logic changes — pure whitespace/quote normalization.

#### Step 2: Fix 3 ruff lint issues

**`flaude/fly_client.py`** — remove unused import (F401). Exact import TBD by reading the file.

**`tests/test_e2e.py`** — rename ambiguous variable `l` (E741). Change to `line` or `log_line`.

**`tests/test_concurrent_integration.py`** — remove unused import (F401). Exact import TBD by reading the file.

After fixes: `uv run ruff check .` must pass with 0 issues.

#### Step 3: Fix 34 mypy errors in `tests/`

Most errors are in `test_exit_code_propagation.py`. Common patterns to fix:
- Add `-> None` return type to test functions that are missing it
- Add type annotations to helper functions
- Fix `Any` type usage (import `Any` from `typing` or use proper types)
- Fix mock/respx type mismatches

Run `uv run mypy flaude/ tests/` after each batch of fixes until 0 errors.

#### Step 4: Add `.pre-commit-config.yaml`

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.3.7
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.9.0
    hooks:
      - id: mypy
        args: [--ignore-missing-imports]
        additional_dependencies:
          - pytest
          - pytest-asyncio
          - respx
          - httpx
```

### Success Criteria:

- [ ] `uv run ruff check .` — 0 issues
- [ ] `uv run ruff format --check .` — 0 files need reformatting
- [ ] `uv run mypy flaude/ tests/` — 0 errors
- [ ] `uv run pytest` — all 372 tests still pass (no logic changed)
- [ ] `pre-commit run --all-files` — passes

---

## Phase 3: CI + Community

### Overview
Two fully independent parallel tracks. CI workflows and community health files have no shared files.

### Dependencies
Requires Phase 2 complete (CI workflows must run against clean code; they will fail on Phase 2 issues if run before).

### Track A: CI workflows

#### `.github/workflows/ci.yml`

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    name: Test (Python ${{ matrix.python-version }})
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          enable-caching: true
      - name: Install dependencies
        run: uv sync --extra dev
      - name: Run tests with coverage
        run: uv run pytest --cov=flaude --cov-report=xml --cov-fail-under=90

  lint:
    name: Lint & Type Check
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          enable-caching: true
      - run: uv sync --extra dev
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy flaude/ tests/
      - run: uv run bandit -r flaude/ -ll
```

Notes:
- Uses `astral-sh/setup-uv@v4` (official uv action) with caching
- Matrix: 3.11, 3.12, 3.13 — validates forward compatibility
- E2E tests excluded by default (`addopts = "-m 'not e2e'"` in `pyproject.toml`)
- No Codecov integration initially — avoids needing `CODECOV_TOKEN` secret setup

#### `.github/workflows/docker.yml`

```yaml
name: Docker

on:
  push:
    branches: [main]
  release:
    types: [published]

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  build-and-push:
    name: Build and push Docker image
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write

    steps:
      - uses: actions/checkout@v4

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          tags: |
            type=raw,value=latest,enable={{is_default_branch}}
            type=sha,prefix=sha-
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}

      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: flaude
          file: flaude/Dockerfile
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
```

Notes:
- Builds from `flaude/Dockerfile` with context `flaude/`
- `latest` tag only on `main` branch pushes
- SHA tags for pinned references in E2E tests
- Semver tags on GitHub releases (matches `make release` tag format)
- Uses `GITHUB_TOKEN` — no secrets to configure

### Track B: Community health files

#### `CODE_OF_CONDUCT.md`

Contributor Covenant v2.1 — standard boilerplate with `conduct@ravi-hq.github.io` as enforcement contact (or leave as "project maintainers" — adjust if there's a preferred contact).

```markdown
# Contributor Covenant Code of Conduct

## Our Pledge

We as members, contributors, and leaders pledge to make participation in our
community a harassment-free experience for everyone...

[Full Contributor Covenant 2.1 text]

## Enforcement

Instances of abusive, harassing, or otherwise unacceptable behavior may be
reported to the project maintainers via GitHub issues or by opening a private
security advisory at https://github.com/ravi-hq/flaude/security/advisories/new.
```

#### `SECURITY.md`

```markdown
# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | ✅        |

## Reporting a Vulnerability

Please do **not** report security vulnerabilities via public GitHub issues.

Instead, use GitHub's private vulnerability reporting:
**[Report a vulnerability](https://github.com/ravi-hq/flaude/security/advisories/new)**

You can expect:
- Acknowledgement within 48 hours
- A fix or mitigation plan within 7 days for critical issues
- Credit in the release notes (unless you prefer to remain anonymous)

## Security Considerations

flaude handles sensitive credentials (Fly.io API tokens, Claude Code OAuth tokens,
GitHub tokens). These are passed as constructor arguments and used only to
authenticate with their respective services. They are never logged, stored, or
transmitted to any service other than their intended target.
```

#### `.github/ISSUE_TEMPLATE/bug_report.yml`

```yaml
name: Bug Report
description: Report a bug in flaude
labels: ["bug"]
body:
  - type: markdown
    attributes:
      value: |
        Thanks for reporting a bug! Please fill out the details below.

  - type: textarea
    id: description
    attributes:
      label: What happened?
      description: A clear description of the bug.
    validations:
      required: true

  - type: textarea
    id: reproduction
    attributes:
      label: Steps to reproduce
      description: Minimal code to reproduce the issue.
      render: python
    validations:
      required: true

  - type: textarea
    id: expected
    attributes:
      label: Expected behavior
      description: What did you expect to happen?
    validations:
      required: true

  - type: input
    id: version
    attributes:
      label: flaude version
      placeholder: "e.g. 0.1.0"
    validations:
      required: true

  - type: input
    id: python
    attributes:
      label: Python version
      placeholder: "e.g. 3.11.8"
    validations:
      required: true
```

#### `.github/ISSUE_TEMPLATE/feature_request.yml`

```yaml
name: Feature Request
description: Suggest a new feature or improvement
labels: ["enhancement"]
body:
  - type: textarea
    id: problem
    attributes:
      label: What problem does this solve?
      description: Describe the use case or limitation you're hitting.
    validations:
      required: true

  - type: textarea
    id: solution
    attributes:
      label: Proposed solution
      description: How would you like this to work?
    validations:
      required: true

  - type: textarea
    id: alternatives
    attributes:
      label: Alternatives considered
      description: Other approaches you've thought about.
```

#### `.github/PULL_REQUEST_TEMPLATE.md`

```markdown
## Summary

<!-- What does this PR do? Why? -->

## Changes

-
-

## Testing

- [ ] Unit tests pass (`make test`)
- [ ] Linting passes (`make check`)
- [ ] Added tests for new behavior (if applicable)

## Related issues

Closes #
```

#### `CHANGELOG.md`

```markdown
# Changelog

All notable changes to flaude will be documented here.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-03-25

### Added
- Initial release
- `MachineConfig` for configuring Fly.io machine execution parameters
- `run_and_destroy()` for fire-and-forget prompt execution
- `stream_and_destroy()` for streaming log output
- `ensure_app()` for idempotent Fly.io app creation
- Automatic machine cleanup via `try/finally` guarantee
- Support for cloning multiple repos into `/workspace`
- Concurrent execution support

[Unreleased]: https://github.com/ravi-hq/flaude/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ravi-hq/flaude/releases/tag/v0.1.0
```

### Success Criteria:

#### Track A (CI):
- [ ] Push to a branch triggers `ci.yml` — test matrix (3.11/3.12/3.13) and lint job run
- [ ] Push to `main` triggers `docker.yml` — image pushed to `ghcr.io/ravi-hq/flaude:latest`
- [ ] `docker pull ghcr.io/ravi-hq/flaude:latest` works

#### Track B (Community):
- [ ] GitHub repo shows "Code of conduct" in Community Standards
- [ ] GitHub repo shows "Security policy" in Security tab
- [ ] New issue creation shows template chooser with Bug Report and Feature Request
- [ ] `.github/ISSUE_TEMPLATE/` directory has both yml files

**Gate**: Verify CI badge URLs resolve correctly before adding to README in Phase 4B.

---

## Phase 4: Release Automation + DX

### Overview
Two parallel tracks. Makefile/release workflow and README updates share no files.

### Dependencies
Requires Phase 3 complete (CI must exist before release workflow can reference it; badges must have backing workflows).

### Track A: Release automation

#### `Makefile`

```makefile
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
```

#### `scripts/release.sh`

```bash
#!/usr/bin/env bash
# Guided release script for flaude.
# Usage: make release (or bash scripts/release.sh directly)
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

fail() { echo -e "${RED}Error: $1${RESET}" >&2; exit 1; }
info() { echo -e "${CYAN}==> $1${RESET}"; }
ok()   { echo -e "${GREEN}✓ $1${RESET}"; }

echo -e "${BOLD}flaude release wizard${RESET}"
echo ""

# --- Preconditions ---
info "Checking preconditions..."

command -v uv >/dev/null 2>&1 || fail "uv is not installed."
command -v git >/dev/null 2>&1 || fail "git is not installed."

if ! git diff --quiet HEAD 2>/dev/null; then
  fail "Working directory is not clean. Commit or stash all changes first."
fi
ok "Working directory clean"

CURRENT_BRANCH=$(git branch --show-current)
if [ "$CURRENT_BRANCH" != "main" ]; then
  fail "Must be on 'main' branch (currently on '$CURRENT_BRANCH')."
fi
ok "On main branch"

git fetch origin main --quiet
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
if [ "$LOCAL" != "$REMOTE" ]; then
  fail "Branch is not up to date with origin/main. Run: git pull origin main"
fi
ok "Up to date with origin/main"

# --- Version ---
CURRENT_VERSION=$(grep '^version' pyproject.toml | sed 's/version = "\(.*\)"/\1/')
echo ""
echo "Current version: ${BOLD}${CURRENT_VERSION}${RESET}"
read -rp "New version (e.g. 0.2.0): " NEW_VERSION

[ -z "$NEW_VERSION" ] && fail "Version cannot be empty."
[[ "$NEW_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "Version must be in X.Y.Z format."

# Check tag doesn't already exist
if git tag -l | grep -q "^v${NEW_VERSION}$"; then
  fail "Tag v${NEW_VERSION} already exists."
fi

# --- Preview ---
echo ""
echo -e "${BOLD}Release plan:${RESET}"
echo "  1. Update pyproject.toml: ${CURRENT_VERSION} → ${NEW_VERSION}"
echo "  2. Open CHANGELOG.md in \$EDITOR for release notes"
echo "  3. Commit: chore: release v${NEW_VERSION}"
echo "  4. Tag: v${NEW_VERSION}"
echo "  5. Push branch + tag → triggers PyPI publish workflow"
echo ""
read -rp "Continue? [y/N] " CONFIRM
[[ "$CONFIRM" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

# --- Execute ---
info "Updating pyproject.toml to v${NEW_VERSION}..."
if [[ "$OSTYPE" == "darwin"* ]]; then
  sed -i '' "s/^version = \".*\"/version = \"${NEW_VERSION}\"/" pyproject.toml
else
  sed -i "s/^version = \".*\"/version = \"${NEW_VERSION}\"/" pyproject.toml
fi
ok "pyproject.toml updated"

info "Opening CHANGELOG.md — add release notes for v${NEW_VERSION}, then save and close..."
${EDITOR:-vi} CHANGELOG.md
ok "CHANGELOG.md updated"

info "Creating release commit..."
git add pyproject.toml CHANGELOG.md
git commit -m "chore: release v${NEW_VERSION}"
ok "Commit created"

info "Creating tag v${NEW_VERSION}..."
git tag -a "v${NEW_VERSION}" -m "Release v${NEW_VERSION}"
ok "Tag v${NEW_VERSION} created"

info "Pushing to origin..."
git push origin main
git push origin "v${NEW_VERSION}"

echo ""
echo -e "${GREEN}${BOLD}Release v${NEW_VERSION} complete!${RESET}"
echo ""
echo "GitHub Actions will now:"
echo "  • Run CI on the tag"
echo "  • Publish to PyPI when CI passes"
echo "  • Build and push Docker image"
echo ""
echo "Monitor: https://github.com/ravi-hq/flaude/actions"
echo "PyPI:    https://pypi.org/project/flaude/"
```

Make executable: `chmod +x scripts/release.sh`

#### `.github/workflows/release.yml`

Uses OIDC trusted publishing — no `PYPI_API_TOKEN` secret required. One-time setup on PyPI: add trusted publisher for `ravi-hq/flaude`, workflow `release.yml`.

```yaml
name: Release

on:
  push:
    tags:
      - 'v*'

jobs:
  publish:
    name: Publish to PyPI
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write  # Required for OIDC trusted publishing

    steps:
      - uses: actions/checkout@v4

      - uses: astral-sh/setup-uv@v4

      - name: Build distribution
        run: uv build

      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
```

**One-time PyPI setup** (do this before first `make release`):
1. Go to https://pypi.org/manage/account/publishing/
2. Add publisher: owner=`ravi-hq`, repo=`flaude`, workflow=`release.yml`, environment=`pypi`
3. Create the `pypi` environment in GitHub repo settings (Settings → Environments → New environment: `pypi`)

#### `.github/dependabot.yml`

```yaml
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
    groups:
      dev-dependencies:
        dependency-type: "development"
      production-dependencies:
        dependency-type: "production"

  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
```

### Track B: README updates

#### `README.md` — add badges and fix dev section

**Add badge block** immediately after the `# flaude` heading (before the description paragraph):

```markdown
[![CI](https://github.com/ravi-hq/flaude/actions/workflows/ci.yml/badge.svg)](https://github.com/ravi-hq/flaude/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/flaude)](https://pypi.org/project/flaude/)
[![Python](https://img.shields.io/pypi/pyversions/flaude)](https://pypi.org/project/flaude/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/ravi-hq/flaude/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-ravi--hq.github.io%2Fflaude-blue)](https://ravi-hq.github.io/flaude)
```

**Add docs link** in the Install section or after the description:
```markdown
Full documentation: **[ravi-hq.github.io/flaude](https://ravi-hq.github.io/flaude)**
```

**Fix Development section** (`README.md:201-209`) — replace `pip install -e ".[dev]"` with `uv sync --extra dev`:

```markdown
## Development

```bash
git clone https://github.com/ravi-hq/flaude.git
cd flaude
uv sync --extra dev      # install all dev dependencies
make test                # run unit tests
make check               # lint + type check + security scan
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full contribution guide.
```

### Success Criteria:

#### Track A (Release):
- [ ] `make help` shows all targets with descriptions
- [ ] `make test` runs pytest
- [ ] `make check` runs ruff + mypy + bandit, all pass
- [ ] `make build` produces `dist/flaude-*.whl` and `dist/flaude-*.tar.gz`
- [ ] `bash scripts/release.sh` — dry-run by running to the version prompt and pressing Ctrl+C (verify precondition checks work)
- [ ] `.github/workflows/release.yml` validates (GitHub Actions lint)
- [ ] `.github/dependabot.yml` validates

#### Track B (DX):
- [ ] README.md renders correctly in GitHub preview (badges show, links work)
- [ ] CI badge URL matches actual workflow filename
- [ ] PyPI badge URL will resolve once package is published (verify format)

---

## Testing Strategy

### Automated (CI):
- Unit tests on Python 3.11, 3.12, 3.13 — 372 tests, ≥90% coverage
- ruff check + format check on every push/PR
- mypy `flaude/` + `tests/` on every push/PR
- bandit security scan on every push/PR

### Manual verification after each phase:
- **Phase 1**: `uv build` → inspect wheel metadata with `unzip -p dist/*.whl '*/METADATA'`
- **Phase 2**: `uv run pytest` all 372 pass; `uv run mypy flaude/ tests/` 0 errors
- **Phase 3A**: push a commit to a branch → verify CI runs in GitHub Actions
- **Phase 3B**: create a test issue in GitHub → verify template chooser appears
- **Phase 4A**: run `make release` through precondition checks; verify PyPI one-time setup docs are accurate
- **Phase 4B**: view README on GitHub → verify badges render (some will be pending until first publish)

## Performance Considerations

- CI matrix (3 Python versions × 2 jobs) will add ~2-3 min to PR feedback. Acceptable.
- uv caching in CI (`enable-caching: true`) keeps install times under 10s.
- Docker builds on every `main` push; `flaude/Dockerfile` is a Node.js image so expect 2-3 min build time. Layer caching not configured initially — add `cache-from: type=gha` to `docker.yml` if this becomes slow.

## One-Time Post-Implementation Steps

These cannot be automated and require manual action after the code is merged:

1. **PyPI trusted publisher setup** — See Phase 4A `release.yml` notes above.
2. **GitHub repo description** — Set "Spin up Fly.io machines to execute Claude Code prompts" in repo settings.
3. **GitHub repo topics** — Add: `fly-io`, `claude`, `claude-code`, `python`, `ai`, `llm`.
4. **GitHub Environments** — Create `pypi` environment in Settings → Environments.
5. **GHCR visibility** — After first Docker push, make the package public: GitHub → Packages → flaude → Package Settings → Change visibility to Public.
6. **`dist/` cleanup** — The manually-built `dist/` artifacts can be deleted from the repo (they shouldn't be committed).

## References

- Research: `thoughts/research/2026-03-25-oss-requirements.md`
- Packaging spec: PEP 621 — https://peps.python.org/pep-0621/
- OIDC publishing: https://docs.pypi.org/trusted-publishers/
- Keep a Changelog: https://keepachangelog.com/en/1.1.0/
- Contributor Covenant: https://www.contributor-covenant.org/version/2/1/code_of_conduct/
