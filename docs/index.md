# flaude

On-demand Claude Code execution on [Fly.io](https://fly.io) machines.

Spin up ephemeral VMs, run Claude Code prompts against your repos, stream the output back,
and auto-destroy the machines when done. No persistent infrastructure required.

## How it works

```
Your code                    Fly.io
───────                      ──────
MachineConfig ──► create VM ──► clone repos
                                  │
                              run Claude Code
                                  │
            ◄── stream logs ◄─ stdout/stderr
                                  │
              destroy VM ◄──── exit
```

1. A Docker container with Claude Code, git, and gh CLI pre-installed boots on Fly.io
2. The entrypoint clones your specified repos into `/workspace`
3. Claude Code runs your prompt in print mode (`-p`)
4. Logs stream back to your process via HTTP log drains (NDJSON)
5. The machine is **always** destroyed after completion (guaranteed via `try/finally`)

## Install

```bash
pip install flaude
```

Requires Python 3.11+. The only runtime dependency is [httpx](https://www.python-httpx.org/).

## Quick start

```python
import asyncio
from flaude import MachineConfig, ensure_app, run_and_destroy

async def main():
    app = await ensure_app("my-flaude-app")

    config = MachineConfig(
        claude_code_oauth_token="sk-ant-oat-...",
        github_username="you",
        github_token="ghp_...",
        prompt="Find and fix any type errors in src/",
        repos=["https://github.com/you/your-repo"],
    )

    result = await run_and_destroy(app.name, config)
    print(f"Exit code: {result.exit_code}")

asyncio.run(main())
```

!!! tip
    Use `run_with_logs` for real-time output — see [Streaming Logs](guide/streaming.md).

## Navigation

- [Overview](overview.md) — why flaude exists and what you can build with it
- [Getting Started](getting-started.md) — prerequisites and first-run tutorial
- [Streaming Logs](guide/streaming.md) — real-time log streaming with `run_with_logs`
- [Concurrent Execution](guide/concurrent.md) — run many prompts in parallel
- [Error Handling](guide/error-handling.md) — handle failures and timeouts
- [Private Repositories](guide/private-repos.md) — authenticate with GitHub
- [Docker Image](guide/docker-image.md) — build and customize the base image
- [Building on flaude](guide/building-on-flaude.md) — advanced integration patterns
- [Architecture](concepts/architecture.md) — how the pieces fit together
- [Log Drain Infrastructure](concepts/log-drain.md) — the log streaming internals
- [API Reference](api/configuration.md) — full API documentation
- [Changelog](changelog.md)
