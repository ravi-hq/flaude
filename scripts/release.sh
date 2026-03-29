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
