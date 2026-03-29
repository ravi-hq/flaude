#!/usr/bin/env bash
set -euo pipefail

# ------------------------------------------------------------------
# flaude entrypoint: clone repos, run Claude Code, signal completion
# ------------------------------------------------------------------

WORKSPACE="${WORKSPACE:-/workspace}"

echo "[flaude] Starting execution"

# --- Validate required environment variables ---
if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    echo "[flaude:error] CLAUDE_CODE_OAUTH_TOKEN is not set" >&2
    exit 1
fi

# --- Clone repos ---
clone_repos() {
    local repos_json="${FLAUDE_REPOS:-}"
    local clone_count=0

    if [ -z "$repos_json" ] || [ "$repos_json" = "[]" ]; then
        echo "[flaude] No repositories to clone"
        return 0
    fi

    # Validate JSON
    if ! echo "$repos_json" | jq empty 2>/dev/null; then
        echo "[flaude:error] FLAUDE_REPOS is not valid JSON" >&2
        return 1
    fi

    echo "[flaude] Cloning repositories..."

    # Configure git credentials for private repos
    if [ -n "${GITHUB_USERNAME:-}" ] && [ -n "${GITHUB_TOKEN:-}" ]; then
        git config --global credential.helper store
        echo "https://${GITHUB_USERNAME}:${GITHUB_TOKEN}@github.com" \
            > ~/.git-credentials
        echo "[flaude] Git credentials configured for ${GITHUB_USERNAME}"
    fi

    local repo_count
    repo_count=$(echo "$repos_json" | jq 'length')

    if [ "$repo_count" -eq 0 ]; then
        echo "[flaude] Empty repos list, nothing to clone"
        return 0
    fi

    for i in $(seq 0 $(( repo_count - 1 ))); do
        local repo_url repo_target

        repo_url=$(echo "$repos_json" | jq -r ".[$i].url // empty")

        if [ -z "$repo_url" ]; then
            echo "[flaude:error] Repo at index $i has no URL, skipping" >&2
            continue
        fi

        repo_target=$(echo "$repos_json" | jq -r ".[$i].target_dir // empty")

        # Default target_dir to repo name derived from URL
        if [ -z "$repo_target" ]; then
            repo_target=$(basename "$repo_url" .git)
        fi

        local clone_args=(--depth 1)

        local repo_branch
        repo_branch=$(echo "$repos_json" | jq -r ".[$i].branch // empty")
        if [ -n "$repo_branch" ]; then
            clone_args+=(--branch "$repo_branch")
        fi

        local target_path="${WORKSPACE}/${repo_target}"

        echo "[flaude] Cloning $repo_url -> $target_path"
        if ! git clone "${clone_args[@]}" "$repo_url" "$target_path"; then
            echo "[flaude:error] Failed to clone $repo_url" >&2
            return 1
        fi

        clone_count=$((clone_count + 1))
        echo "[flaude] Cloned $repo_url successfully ($clone_count/$repo_count)"
    done

    echo "[flaude] All $clone_count repositories cloned"

    # Set working directory: if exactly one repo, cd into it
    if [ "$clone_count" -eq 1 ]; then
        local single_target
        single_target=$(echo "$repos_json" | jq -r '.[0].target_dir // empty')
        if [ -z "$single_target" ]; then
            single_target=$(basename "$(echo "$repos_json" | jq -r '.[0].url')" .git)
        fi
        WORKSPACE="${WORKSPACE}/${single_target}"
        echo "[flaude] Working directory set to $WORKSPACE"
    fi

    return 0
}

# Run repo cloning
clone_repos

# --- Run Claude Code ---
if [ -z "${FLAUDE_PROMPT:-}" ]; then
    echo "[flaude:error] FLAUDE_PROMPT is not set" >&2
    exit 1
fi

echo "[flaude] Running Claude Code in $WORKSPACE ..."

cd "$WORKSPACE"

# Build optional output format arguments
output_fmt_args=()
if [ -n "${FLAUDE_OUTPUT_FORMAT:-}" ]; then
    output_fmt_args+=(--output-format "$FLAUDE_OUTPUT_FORMAT")
    # stream-json requires --verbose
    if [ "$FLAUDE_OUTPUT_FORMAT" = "stream-json" ]; then
        output_fmt_args+=(--verbose)
    fi
fi

# Run Claude Code in non-interactive/print mode with the prompt.
# -p (--print) sends prompt as a one-shot and streams output to stdout.
# Use -- to prevent prompts starting with "-" from being parsed as flags.
# Temporarily disable set -e so we can capture the exit code and log it.
set +e
claude -p "${output_fmt_args[@]}" -- "$FLAUDE_PROMPT"
EXIT_CODE=$?
set -e

echo "[flaude] Claude Code exited with code $EXIT_CODE"
echo "[flaude:exit:$EXIT_CODE]"

exit $EXIT_CODE
