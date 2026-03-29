# Release Skill Implementation Plan

## Overview

Create a `/release` skill for flaude and the supporting shell scripts (`Makefile`, `scripts/release.sh`) so that new versions can be released both via Claude Code and from the terminal. This completes Phase 4A of the OSS requirements plan while adding Claude integration.

## Research Summary

Research conducted by agent team with 4 specialist tracks:
- **Versioning**: Version `0.1.0` hardcoded in `pyproject.toml:7`, hatchling build, CHANGELOG follows Keep a Changelog format. No dynamic versioning.
- **CI/CD**: `publish.yml` triggers on GitHub Release published event (not tag push). Docker workflow tags GHCR images on release. No git tags exist yet.
- **Docs**: OSS plan (`thoughts/plans/2026-03-26-oss-requirements.md`) specifies `Makefile` + `scripts/release.sh` in Phase 4A but they were never created. `release.sh` spec pushes bare tags but `publish.yml` needs a GitHub Release event.
- **Skills**: Skills live at `~/.claude/skills/<name>/SKILL.md` with YAML frontmatter. `/ship` is the closest analog (handles PR creation). No skill covers tag → GitHub Release → PyPI flow.

### Key Discoveries:
- `pyproject.toml:7` — `version = "0.1.0"` is the sole version source of truth
- `.github/workflows/publish.yml:4-5` — triggers on `release: types: [published]`, NOT tag push
- `thoughts/plans/2026-03-26-oss-requirements.md:813` — `release.sh` says "push tag → triggers PyPI publish" but this is wrong: the actual trigger is a GitHub Release event
- `CHANGELOG.md:8` — empty `[Unreleased]` section ready for entries
- No git tags exist — v0.1.0 was committed to CHANGELOG but never formally tagged/released

### Critical Fix:
The OSS plan's `release.sh` pushes a bare tag and claims that triggers PyPI publish. It does NOT — `publish.yml` requires a GitHub Release event. The fix: use `gh release create v${VERSION} --title "v${VERSION}" --notes-file -` after pushing the tag. This creates the GitHub Release which triggers both `publish.yml` (PyPI) and `docker.yml` (GHCR versioned tags). No separate `release.yml` workflow needed — the existing `publish.yml` already handles it correctly.

## Current State Analysis

**Exists:**
- `pyproject.toml` with static version `0.1.0`
- `CHANGELOG.md` with Keep a Changelog format
- `.github/workflows/publish.yml` (PyPI via OIDC on GitHub Release)
- `.github/workflows/docker.yml` (GHCR on push to main + GitHub Release)
- `.github/workflows/ci.yml` (tests + lint on push/PR)

**Missing:**
- `Makefile` (planned in OSS Phase 4A, never created)
- `scripts/release.sh` (planned in OSS Phase 4A, never created)
- `/release` skill for Claude Code
- Registration in `~/CLAUDE.md`
- Release process documentation in `CONTRIBUTING.md`

## Desired End State

- `make release` from terminal walks through version bump → CHANGELOG → commit → tag → push → GitHub Release
- `/release` skill in Claude Code does the same interactively (asks version bump type, edits CHANGELOG via Edit tool, creates commit/tag/push/release)
- Both paths end with `gh release create` which triggers the existing `publish.yml` and `docker.yml`
- `CONTRIBUTING.md` documents the release process for maintainers
- Skill registered in `~/CLAUDE.md`

## What We're NOT Doing

- No dynamic versioning (hatch-vcs, commitizen, setuptools-scm)
- No pre-release versions (rc, alpha, beta)
- No separate `release.yml` workflow (existing `publish.yml` handles it)
- No `.github/dependabot.yml` (separate concern, not release-related)
- No auto-generated changelogs from commits
- Not touching v0.1.0 — it was already released as a special case

---

## File Ownership Map

Designed for parallel execution via `team-implement`:

| File | Phase | Owner Track | Change Type |
|------|-------|-------------|-------------|
| `Makefile` | 1 | backend | create |
| `scripts/release.sh` | 1 | backend | create |
| `.claude/skills/release/SKILL.md` | 2 | skill | create |
| `CLAUDE.md` | 3 | docs | modify |
| `CONTRIBUTING.md` | 3 | docs | modify |

**Conflict-free guarantee**: No file appears in multiple owner tracks within the same phase. Phase 2 depends on Phase 1 (skill needs to know scripts interface). Phase 3 depends on both.

---

## Phase 1: Release Scripts

### Overview
Create `Makefile` and `scripts/release.sh` — the terminal-based release flow. Based on the OSS plan spec but **fixed** to use `gh release create` instead of bare tag push.

### Changes Required:

#### 1. `Makefile` (create)

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

#### 2. `scripts/release.sh` (create)

Based on the OSS plan spec with these changes:
- **Added**: `gh release create` step after pushing tag (fixes the publish trigger)
- **Added**: `gh` CLI precondition check
- **Added**: Extract release notes from CHANGELOG for the GitHub Release body
- **Removed**: Misleading comment about tag push triggering PyPI

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
command -v gh >/dev/null 2>&1 || fail "gh CLI is not installed. Install: https://cli.github.com/"

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
echo "  5. Push branch + tag"
echo "  6. Create GitHub Release → triggers PyPI publish + Docker build"
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
ok "Pushed to origin"

# Extract release notes from CHANGELOG for GitHub Release body
info "Creating GitHub Release..."
RELEASE_NOTES=$(awk "/^## \[${NEW_VERSION}\]/{found=1; next} /^## \[/{if(found) exit} found" CHANGELOG.md)
echo "$RELEASE_NOTES" | gh release create "v${NEW_VERSION}" \
  --title "v${NEW_VERSION}" \
  --notes-file -
ok "GitHub Release created"

echo ""
echo -e "${GREEN}${BOLD}Release v${NEW_VERSION} complete!${RESET}"
echo ""
echo "GitHub Actions will now:"
echo "  • Publish to PyPI (via OIDC trusted publishing)"
echo "  • Build and push Docker image to ghcr.io"
echo ""
echo "Monitor: https://github.com/ravi-hq/flaude/actions"
echo "PyPI:    https://pypi.org/project/flaude/${NEW_VERSION}/"
```

Make executable: `chmod +x scripts/release.sh`

### Success Criteria:

#### Automated Verification:
- [ ] `make help` shows all targets with descriptions
- [ ] `make test` runs pytest
- [ ] `make check` runs ruff + mypy + bandit
- [ ] `bash -n scripts/release.sh` passes (syntax check)
- [ ] `scripts/release.sh` is executable

#### Manual Verification:
- [ ] `make release` on a dirty working directory fails with clear error
- [ ] `make release` on a non-main branch fails with clear error

**Gate**: Pause for human review before proceeding to Phase 2.

---

## Phase 2: Release Skill

### Overview
Create the `/release` Claude Code skill at `.claude/skills/release/SKILL.md` (in-repo, not global). This skill does the same thing as `scripts/release.sh` but interactively within Claude Code — using Edit tool for CHANGELOG, AskUserQuestion for version selection, and Bash for git/gh commands.

### Dependencies
- Requires Phase 1 complete (skill references the Makefile/scripts and follows the same flow)

### Changes Required:

#### 1. `~/.claude/skills/release/SKILL.md` (create)

The skill should:

1. **Frontmatter**: name `release`, description mentioning "release", "version", "publish", "tag". Allowed tools: Bash, Read, Edit, Grep, Glob, AskUserQuestion.

2. **Step 1 — Pre-flight checks** (via Bash):
   - Verify on `main` branch
   - Verify working directory is clean
   - Verify up to date with origin/main
   - Verify `gh` CLI is available and authenticated
   - Read current version from `pyproject.toml`

3. **Step 2 — Determine new version** (via AskUserQuestion):
   - Parse current version into major.minor.patch
   - Present options: patch (0.1.0 → 0.1.1), minor (0.1.0 → 0.2.0), major (0.1.0 → 1.0.0), or custom
   - If user passed version as argument, skip the question

4. **Step 3 — Update version** (via Edit):
   - Edit `pyproject.toml` to update the `version = "X.Y.Z"` line

5. **Step 4 — Update CHANGELOG** (via Edit):
   - Read current `CHANGELOG.md`
   - Move `[Unreleased]` content to new `## [X.Y.Z] - YYYY-MM-DD` section
   - Add new empty `[Unreleased]` section
   - Update comparison links at bottom
   - Show the user the changes and ask for confirmation

6. **Step 5 — Commit, tag, push** (via Bash):
   - `git add pyproject.toml CHANGELOG.md`
   - `git commit -m "chore: release vX.Y.Z"`
   - `git tag -a vX.Y.Z -m "Release vX.Y.Z"`
   - `git push origin main`
   - `git push origin vX.Y.Z`

7. **Step 6 — Create GitHub Release** (via Bash):
   - Extract release notes from CHANGELOG (the version section just created)
   - `gh release create vX.Y.Z --title "vX.Y.Z" --notes "..."`
   - Report success with links to Actions, PyPI, GHCR

8. **Step 7 — Verify** (via Bash):
   - Check GitHub Actions status: `gh run list --limit 3`
   - Report the publish workflow status

The skill should NOT include gstack preamble/telemetry — this is a project-specific skill, not a gstack skill.

### Success Criteria:

#### Automated Verification:
- [ ] `.claude/skills/release/SKILL.md` exists with valid YAML frontmatter
- [ ] Frontmatter has `name`, `description`, and `allowed-tools` fields

#### Manual Verification:
- [ ] Invoke `/release` in Claude Code — it should detect current version and offer bump options
- [ ] The CHANGELOG editing step shows a preview before confirming
- [ ] Pre-flight catches dirty working directory, wrong branch, out-of-date branch

**Gate**: Pause for human review before proceeding to Phase 3.

---

## Phase 3: Integration & Documentation

### Overview
Register the skill in `~/CLAUDE.md` and document the release process in `CONTRIBUTING.md`.

### Dependencies
- Requires Phase 1 and Phase 2 complete

### Changes Required:

#### 1. `~/CLAUDE.md` (modify)
Add to the `### Available skills` list:
```
- `/release` - Release a new version (bump version, changelog, tag, publish)
```

#### 2. `CONTRIBUTING.md` (modify)
Add a new section after "Submitting a pull request" (after line 44):

```markdown
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
```

### Success Criteria:

#### Automated Verification:
- [ ] `grep '/release' ~/CLAUDE.md` finds the skill entry
- [ ] `grep -c 'Releasing a new version' CONTRIBUTING.md` returns 1

#### Manual Verification:
- [ ] `/release` appears in Claude Code skill suggestions
- [ ] CONTRIBUTING.md reads coherently with the new section

---

## Testing Strategy

### Automated:
- `bash -n scripts/release.sh` — syntax validation
- `make help` — Makefile parses correctly
- Pre-flight checks in the script catch bad state (dirty tree, wrong branch, stale branch, missing tools)

### Manual Testing Steps:
1. Run `make release` on a dirty working directory — should fail with clear error
2. Run `make release` on a feature branch — should fail
3. Run `/release` in Claude Code — should detect version 0.1.0, offer bump options
4. Dry-run the full flow on a test branch (or fork) to verify end-to-end

### What we can't test without a real release:
- OIDC trusted publishing to PyPI (requires the `pypi` environment on GitHub)
- Docker image tagging on GHCR
- These are already proven by the existing workflows — we're just triggering them correctly

## Performance Considerations

None — this is a human-interactive release flow that runs infrequently.

## References

- OSS requirements plan: `thoughts/plans/2026-03-26-oss-requirements.md` (Phase 4A)
- Existing publish workflow: `.github/workflows/publish.yml`
- Existing docker workflow: `.github/workflows/docker.yml`
- CHANGELOG format: [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)
- Skill format reference: `~/.claude/skills/ship/SKILL.md`
