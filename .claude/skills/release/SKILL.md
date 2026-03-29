---
name: release
version: 1.0.0
description: |
  Release a new version of flaude. Bumps version in pyproject.toml, updates CHANGELOG.md,
  commits, tags, pushes, and creates a GitHub Release (which triggers PyPI publish + Docker build).
  Use when asked to "release", "cut a release", "bump version", "publish", or "new version".
allowed-tools:
  - Bash
  - Read
  - Edit
  - Grep
  - Glob
  - AskUserQuestion
---

# /release — New Version Release

Release a new version of flaude. This skill mirrors `make release` / `scripts/release.sh`
but runs interactively within Claude Code.

## Only stop for:
- Pre-flight check failures (dirty tree, wrong branch, out of date)
- User version selection
- CHANGELOG review confirmation
- Git push / GitHub Release confirmation

## Never stop for:
- Reading files
- Computing version bumps
- Creating commits or tags (after user confirmed)

---

## Step 1: Pre-flight Checks

Run these checks via Bash. If any fail, report the error and stop.

```bash
# Check we're on main
BRANCH=$(git branch --show-current)
[ "$BRANCH" = "main" ] || echo "FAIL: not on main (on $BRANCH)"

# Check working directory is clean
git diff --quiet HEAD 2>/dev/null || echo "FAIL: working directory not clean"

# Check up to date with origin
git fetch origin main --quiet
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
[ "$LOCAL" = "$REMOTE" ] || echo "FAIL: not up to date with origin/main"

# Check gh CLI
command -v gh >/dev/null 2>&1 || echo "FAIL: gh CLI not installed"
gh auth status 2>/dev/null || echo "FAIL: gh not authenticated"

# Current version
grep '^version' pyproject.toml
```

If any check fails, tell the user what to fix and stop.

## Step 2: Determine New Version

Read current version from `pyproject.toml`. Parse into MAJOR.MINOR.PATCH.

If the user provided a version as an argument (e.g., `/release 0.2.0`), use that directly
after validating it matches X.Y.Z format and the tag doesn't already exist.

Otherwise, use AskUserQuestion:

```
Current version: **{CURRENT}**

What type of release?

A) Patch ({MAJOR}.{MINOR}.{PATCH+1}) — bug fixes
B) Minor ({MAJOR}.{MINOR+1}.0) — new features, backwards compatible
C) Major ({MAJOR+1}.0.0) — breaking changes
D) Custom version number
```

After selection, verify the tag `v{NEW_VERSION}` doesn't already exist:
```bash
git tag -l "v{NEW_VERSION}"
```

## Step 3: Update pyproject.toml

Use the Edit tool to change the version line:

```
old: version = "{CURRENT_VERSION}"
new: version = "{NEW_VERSION}"
```

## Step 4: Update CHANGELOG.md

Read `CHANGELOG.md`. Use the Edit tool to:

1. Replace the `## [Unreleased]` section — keep the heading but ensure content below it is empty
2. Insert a new `## [{NEW_VERSION}] - {TODAY_YYYY-MM-DD}` section between `[Unreleased]` and the previous version
3. If the `[Unreleased]` section has content, move it under the new version heading
4. If the `[Unreleased]` section is empty, add a placeholder and ask the user what changed:
   - Use AskUserQuestion: "The [Unreleased] section is empty. What should go in the release notes for v{NEW_VERSION}? (I'll format it as Keep a Changelog entries)"
5. Update the comparison links at the bottom:
   - `[Unreleased]` link: compare `v{NEW_VERSION}...HEAD`
   - `[{NEW_VERSION}]` link: compare `v{PREVIOUS_VERSION}...v{NEW_VERSION}`

Show the user the final CHANGELOG diff and ask for confirmation before proceeding.

## Step 5: Commit, Tag, and Push

After the user confirms the CHANGELOG:

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "chore: release v{NEW_VERSION}"
git tag -a "v{NEW_VERSION}" -m "Release v{NEW_VERSION}"
```

Then ask the user to confirm before pushing:

```
Ready to push and create the GitHub Release:
- Push commit + tag to origin/main
- Create GitHub Release v{NEW_VERSION}
- This will trigger PyPI publish + Docker image build

Proceed?
```

After confirmation:

```bash
git push origin main
git push origin "v{NEW_VERSION}"
```

## Step 6: Create GitHub Release

Extract the release notes for this version from CHANGELOG.md (the content between
`## [{NEW_VERSION}]` and the next `## [` heading). Then create the GitHub Release:

```bash
gh release create "v{NEW_VERSION}" --title "v{NEW_VERSION}" --notes "{RELEASE_NOTES}"
```

## Step 7: Verify and Report

Check the triggered workflows:

```bash
gh run list --limit 3
```

Report to the user:

```
Release v{NEW_VERSION} complete!

GitHub Actions triggered:
- Publish to PyPI (OIDC trusted publishing)
- Docker image build → ghcr.io/ravi-hq/flaude:{NEW_VERSION}

Monitor: https://github.com/ravi-hq/flaude/actions
PyPI: https://pypi.org/project/flaude/{NEW_VERSION}/
```
