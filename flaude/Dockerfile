# flaude: Claude Code execution environment for Fly.io machines
# Base: Node.js 22 on Debian Bookworm (slim) — provides npm for Claude Code
FROM node:22-bookworm-slim

# Avoid interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies: git, curl, gpg (for gh CLI repo setup)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    gpg \
    ca-certificates \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Install GitHub CLI (gh) from official apt repository
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      | gpg --dearmor -o /usr/share/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends gh && \
    rm -rf /var/lib/apt/lists/*

# Install Claude Code globally via npm
RUN npm install -g @anthropic-ai/claude-code && \
    npm cache clean --force

# Create a non-root user for Claude Code (--dangerously-skip-permissions
# refuses to run as root for security reasons)
RUN useradd -m -s /bin/bash claude

# Create working directories owned by the non-root user
RUN mkdir -p /workspace /data && chown claude:claude /workspace /data
WORKDIR /workspace

# Copy entrypoint script
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Environment variables expected at runtime (set by Fly machine config):
#   CLAUDE_CODE_OAUTH_TOKEN  — auth token for Claude Code
#   GITHUB_USERNAME          — for git clone auth
#   GITHUB_TOKEN             — for git clone auth
#   FLAUDE_REPOS             — JSON array of repo URLs to clone
#   FLAUDE_PROMPT            — the prompt string to pass to Claude Code
#   FLAUDE_OUTPUT_FORMAT     — output format: "stream-json", "json", or omit for text

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
