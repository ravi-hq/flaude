---
date: 2026-03-25T06:05:00+00:00
researcher: Claude Code (team-research skill)
git_commit: ea1194cf92cb79942c3a3ca2cba82cee29c411d4
branch: main
repository: ravi-hq/flaude
topic: "What does flaude need to meet modern open source project expectations?"
tags: [research, team-research, oss, ci-cd, packaging, community, quality, dx]
status: complete
method: agent-team
team_size: 5
tracks: [cicd, quality-tooling, community-health, packaging, developer-experience]
last_updated: 2026-03-25
last_updated_by: Claude Code
---

# Research: Modern Open Source Project Requirements for flaude

**Date**: 2026-03-25
**Researcher**: Claude Code (team-research)
**Git Commit**: [`ea1194c`](https://github.com/ravi-hq/flaude/commit/ea1194cf92cb79942c3a3ca2cba82cee29c411d4)
**Branch**: `main`
**Repository**: ravi-hq/flaude
**Method**: Agent team (5 specialist researchers)

## Research Question

What does flaude need to meet the expectations of a modern open source Python project?

## Summary

flaude has strong fundamentals: 372 passing tests, 94% coverage, clean mypy on source, a good README, comprehensive MkDocs docs site, and solid AGENTS.md for AI-assisted development. However, it is not yet ready for public OSS distribution. The most critical gaps are: **no CI workflows** (only docs deploy exists), **incomplete PyPI metadata** (blank PyPI page would result from publishing today), **missing community health files** (CODE_OF_CONDUCT, SECURITY, issue templates), and **broken references in existing docs** (CONTRIBUTING.md links to files that don't exist). Two CI checks (pytest + mypy on source) could be added with zero code changes today.

## Research Tracks

### Track 1: CI/CD & Automation
**Researcher**: cicd-researcher
**Scope**: `.github/workflows/`, `pyproject.toml`, Dockerfile, uv.lock

#### Findings:
1. **Only one workflow exists** — `.github/workflows/docs.yml` deploys MkDocs to GitHub Pages on push to `main`. No other automation exists.
2. **No test CI** — 372 unit tests are never automatically run. No workflow validates PRs or pushes against the test suite.
3. **No lint/format/type-check CI** — CONTRIBUTING.md documents `ruff check`, `ruff format --check`, and `mypy flaude/` as required, but none run in CI. Entirely honor-system.
4. **No Docker image build/push workflow** — `flaude/Dockerfile` is built and pushed manually. E2E tests depend on a pre-pushed image with no automation.
5. **No PyPI publish workflow** — No workflow to publish on version tag push. `dist/` artifacts were built manually.
6. **No pre-commit hooks** — No `.pre-commit-config.yaml`. Nothing enforces quality at commit time.
7. **No dependabot/renovate** — No automated dependency update config.
8. **No action SHA pinning** — Existing `docs.yml` uses `actions/checkout@v4` without SHA pinning (security best practice for OSS).

**Two zero-friction CI wins** (no code changes needed):
- `pytest` (unit tests) — passes today with 372/372
- `mypy flaude/` (source only) — passes today with 0 errors

---

### Track 2: Code Quality & Tooling
**Researcher**: quality-researcher
**Scope**: `pyproject.toml`, source files, test files

#### Findings:
1. **Test suite is strong** — 372 unit tests passing, 94% overall coverage (most modules 95–100%; `fly_client.py` at 60% is the main outlier).
2. **mypy clean on source** — `mypy flaude/` passes with 0 errors. `from __future__ import annotations` used consistently. 34 mypy errors in `tests/` (type mismatches, untyped functions), mostly in `test_exit_code_propagation.py`.
3. **ruff not configured** — No `[tool.ruff]` section in `pyproject.toml`. Running `ruff check` manually finds 3 real issues (unused import in `fly_client.py`; ambiguous variable `l` in `test_e2e.py`; unused import in `test_concurrent_integration.py`).
4. **ruff format not enforced** — `ruff format --check` would flag 24 of 31 files as needing reformatting. No consistent style enforced.
5. **No coverage config** — No `[tool.coverage]` in `pyproject.toml`, no `--fail-under` threshold, no CI enforcement.
6. **No pre-commit config** — `.pre-commit-config.yaml` absent.
7. **ruff and mypy not in dev dependencies** — `pyproject.toml` `[dev]` extras include only `pytest`, `pytest-asyncio`, `respx`. A fresh `uv sync --extra dev` won't install the tools CONTRIBUTING.md says are required.
8. **No `py.typed` marker** — Package doesn't declare itself as typed (PEP 561), despite being well-typed.
9. **No security scanner** — `bandit` not installed. Project handles API tokens and credentials in env vars; `bandit -r flaude/ -ll` would be prudent.

---

### Track 3: Community Health Files
**Researcher**: community-researcher
**Scope**: project root, `.github/`, `docs/`

#### Findings:
1. **CODE_OF_CONDUCT.md missing** — GitHub prominently surfaces this. No community standards statement exists.
2. **SECURITY.md missing** — No vulnerability disclosure process. GitHub Security tab flags this.
3. **`.github/ISSUE_TEMPLATE/` directory missing** — CONTRIBUTING.md actively links to `bug_report.yml` and `feature_request.yml` that don't exist → 404s for contributors.
4. **`.github/PULL_REQUEST_TEMPLATE.md` missing** — Contributors get no structured PR guidance.
5. **CHANGELOG.md missing** — No release history for users upgrading.
6. **CODEOWNERS missing** — No automated review assignment.
7. **`.env.example` missing** — CONTRIBUTING.md instructs `cp .env.example .env` but the file doesn't exist. Blocks first-time contributor E2E setup immediately.
8. **CONTRIBUTING.md has placeholder clone URL** — Uses `github.com/YOUR_USERNAME/flaude.git` instead of actual repo URL.
9. **What exists and is good**: `LICENSE` (MIT, 2026), `CONTRIBUTING.md` (covers setup, tests, linting, PR process — substantive), `README.md` (comprehensive quick start, API reference).

---

### Track 4: Package Metadata & Distribution
**Researcher**: packaging-researcher
**Scope**: `pyproject.toml`, `LICENSE`, `dist/`

#### Findings:
1. **`readme` field missing** — No `readme = "README.md"` in `[project]`. PyPI page long description would be blank if published today. (`pyproject.toml:5-12`)
2. **`authors` missing** — No author or maintainer metadata. (`pyproject.toml` — field absent)
3. **`license` field missing** — `LICENSE` file exists (MIT) but PEP 621 requires `license = {file = "LICENSE"}` in `[project]`. (`pyproject.toml` — field absent)
4. **`keywords` missing** — Not declared. (`pyproject.toml` — field absent)
5. **`classifiers` missing** — No `Programming Language :: Python :: 3.11`, no `License :: OSI Approved :: MIT License`, no `Development Status`, no `Intended Audience`. Undiscoverable on PyPI. (`pyproject.toml` — section absent)
6. **`[project.urls]` missing** — No Homepage, Documentation, Source, Changelog, or Bug Tracker links. (`pyproject.toml` — section absent)
7. **`requires-python` is correct** — `requires-python = ">=3.11"` at `pyproject.toml:9`. ✓
8. **Version is hardcoded** — `version = "0.1.0"` at `pyproject.toml:7`. No `hatch-vcs`, no `dynamic = ["version"]`, no git tags. Manual version bumps required.
9. **No CHANGELOG.md** — No automated changelog tooling (git-cliff, towncrier, commitizen).
10. **No PyPI publish workflow** — `dist/` artifacts appear to have been built and pushed manually.
11. **`flaude/Dockerfile` and `flaude/entrypoint.sh` are in the wheel** — Intentional (needed at runtime), but worth noting.

---

### Track 5: Developer Experience & README Quality
**Researcher**: dx-researcher
**Scope**: `README.md`, project root, `docs/`, `.github/`

#### Findings:
1. **No badges in README** — No CI status, PyPI version, Python versions, license, or coverage badges. (`README.md` — badges section absent)
2. **No `.devcontainer/`** — No GitHub Codespaces or VS Code devcontainer support. (missing)
3. **No Makefile or taskfile** — `pyproject.toml:39-42` has hatch docs scripts only. Contributors must read docs to know `uv run pytest`, `uv run ruff check .`, etc.
4. **README dev section inconsistent with CONTRIBUTING** — `README.md:201` uses `pip install -e ".[dev]"` while `CONTRIBUTING.md` uses `uv`. Conflicting onboarding paths.
5. **AGENTS.md and CLAUDE.md both present** — Well-maintained AI-optimized references. Positive differentiator. (`AGENTS.md`, `CLAUDE.md`)
6. **No link to hosted docs in README** — MkDocs site exists and auto-deploys to GitHub Pages but README doesn't link to it. `mkdocs.yml` has no `site_url` or `repo_url`.
7. **`.gitignore` minimal** — Covers basics but missing: `__pycache__/`, `*.egg-info/`, `build/`, `htmlcov/`, `*.pyo`, `.tox/`. (`.gitignore:1-10`)
8. **`repo_url` / `site_url` missing from `mkdocs.yml`** — Standard Material theme fields for edit buttons and canonical URLs.

---

## Cross-Track Discoveries

- **The dev dependency gap is a compounding problem**: `ruff` and `mypy` are documented as required in CONTRIBUTING.md but absent from `[dev]` extras (Track 2) — so the CI workflows that don't exist (Track 1) couldn't even install them without fixing this first.
- **CONTRIBUTING.md is a single point of breakage**: It references `.env.example` (missing), issue templates (missing), and uses a placeholder repo URL — three separate gaps all concentrated in the contributor onboarding document.
- **Zero-friction CI path exists**: `pytest` + `mypy flaude/` both pass cleanly today. These two CI checks could go live immediately, providing value before the format/lint cleanup work is done.
- **Docs are ahead of code hygiene**: The MkDocs site is comprehensive and auto-deploys, but the package it documents isn't properly configured for PyPI distribution (no readme field, no classifiers, no URLs).

---

## Code References

| File | Track(s) | Key Gap |
|------|----------|---------|
| `pyproject.toml:5-12` | 2, 4 | Missing: readme, authors, license, keywords, classifiers, [project.urls] |
| `pyproject.toml:7` | 4 | Version hardcoded, no dynamic versioning |
| `pyproject.toml:15-18` | 2 | dev extras missing ruff, mypy, coverage, bandit, pre-commit |
| `.github/workflows/` | 1 | Only docs.yml; no test/lint/publish/docker workflows |
| `CONTRIBUTING.md` | 3, 5 | Broken links to issue templates + .env.example; placeholder URL |
| `.gitignore` | 5 | Missing __pycache__/, *.egg-info/, build/, htmlcov/ |
| `mkdocs.yml` | 5 | Missing site_url, repo_url |

---

## Architecture Insights

- **Build system is modern**: hatchling + uv is a solid, modern Python packaging stack. No legacy setup.py/setup.cfg debt.
- **Test architecture is thorough**: 20+ test files with respx mocking for HTTP calls, separate E2E suite gated by markers. The quality foundation is genuinely good.
- **Source code is well-typed**: mypy passes cleanly on all 10 source files with consistent annotation style. `py.typed` marker is the only missing piece.
- **AI-first documentation**: AGENTS.md is a rare and valuable addition that most OSS projects lack.

---

## Prioritized Gap List

### P0 — Fix broken references (no new files needed)
- Add `ruff`, `mypy`, `coverage[toml]` to `[dev]` extras in `pyproject.toml`
- Fix CONTRIBUTING.md placeholder clone URL
- Add `__pycache__/`, `*.egg-info/`, `build/`, `htmlcov/` to `.gitignore`
- Add `site_url` and `repo_url` to `mkdocs.yml`

### P1 — Zero-friction CI (two checks, no code changes)
- Add `.github/workflows/ci.yml`: `pytest` on push/PR (passes today)
- Add mypy source check to CI workflow (passes today)

### P2 — Complete package metadata (pyproject.toml additions)
- Add `readme = "README.md"`
- Add `license = {file = "LICENSE"}`
- Add `authors`, `keywords`, `classifiers`
- Add `[project.urls]` (Homepage, Documentation, Source, Bug Tracker)

### P3 — Community health files (new files)
- `CODE_OF_CONDUCT.md` (Contributor Covenant is standard)
- `SECURITY.md` (vulnerability disclosure process)
- `.github/ISSUE_TEMPLATE/bug_report.yml`
- `.github/ISSUE_TEMPLATE/feature_request.yml`
- `.github/PULL_REQUEST_TEMPLATE.md`
- `.env.example`
- `CHANGELOG.md`

### P4 — Code quality enforcement
- Add `[tool.ruff.lint]`, `[tool.ruff.format]`, `[tool.mypy]`, `[tool.coverage.report]` to `pyproject.toml`
- Run `ruff format .` (one-time pass on 24 files)
- Fix 3 ruff lint issues
- Add CI steps for ruff check + format
- Add `.pre-commit-config.yaml`
- Add `py.typed` marker to `flaude/`

### P5 — Release automation
- Add `.github/workflows/release.yml` (publish to PyPI on version tag)
- Add `.github/workflows/docker.yml` (build/push container image)
- Configure dynamic versioning (`hatch-vcs`) or commitizen
- Add dependabot/renovate config

### P6 — Developer experience polish
- Add README badges (CI status, PyPI, Python, license, coverage)
- Add link to hosted docs in README
- Add Makefile with `test`, `lint`, `format`, `docs`, `build` targets
- Reconcile README dev section with CONTRIBUTING (pip vs uv)
- Consider `.devcontainer/` for Codespaces support

---

## Open Questions

- **PyPI publishing**: Is flaude intended for public PyPI distribution now, or is the 0.1.0 in `dist/` a private build? Determines urgency of P2/P5.
- **Docker registry**: Where should the built container image live? `registry.fly.io/flaude` requires a Fly.io app to exist; a public GHCR image might be better for OSS.
- **Versioning strategy**: Commitizen (conventional commits → auto CHANGELOG) or simple manual tags? Shapes both the release workflow and CHANGELOG approach.
- **mypy test strictness**: Should test files eventually be fully typed (34 issues to fix), or is source-only mypy the target?
