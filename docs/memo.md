# flaude: Project Memo

**Date:** 2026-03-27
**Version:** 0.1.0
**Status:** Published on PyPI

## What is flaude?

flaude is a Python library that lets you run Claude Code on ephemeral Fly.io virtual machines. You give it a prompt and a list of repos. It spins up a VM, clones the repos, runs Claude Code against them, streams the output back to you, and destroys the machine when it's done.

The whole lifecycle is managed for you. The machine is always cleaned up, even if your code crashes, the prompt fails, or the process is cancelled. You write `run_and_destroy()` and it does what it says.

## Why does this exist?

Running Claude Code locally works fine for one-off tasks. But if you want to run Claude Code programmatically, on remote infrastructure, against repos you don't have cloned locally, or run many prompts in parallel, you need something else.

Before flaude, you'd have to:

1. Write Fly.io API calls to create machines
2. Build and push a Docker image with Claude Code installed
3. Handle machine lifecycle (create, wait, poll for exit, destroy)
4. Parse exit codes from Fly's event API (which has three different formats)
5. Set up log streaming via HTTP log drains
6. Guarantee cleanup on every code path (success, failure, timeout, cancellation)

flaude packages all of that into a single `pip install`.

## How to install

```bash
pip install flaude
```

Python 3.11+. The only runtime dependency is httpx.

## How to use it

### Prerequisites

You need three things:

1. **A Fly.io account** with `FLY_API_TOKEN` set in your environment
2. **A Claude Code OAuth token** (`CLAUDE_CODE_OAUTH_TOKEN`)
3. **GitHub credentials** if you're cloning private repos

### The simplest use case: run a prompt

```python
import asyncio
from flaude import MachineConfig, ensure_app, run_and_destroy

async def main():
    # Create (or reuse) a Fly.io app
    app = await ensure_app("my-flaude-app")

    # Configure what you want Claude Code to do
    config = MachineConfig(
        claude_code_oauth_token="sk-ant-oat-...",
        prompt="Find and fix any type errors in src/",
        repos=["https://github.com/you/your-repo"],
    )

    # Run it. Machine is created, prompt runs, machine is destroyed.
    result = await run_and_destroy(app.name, config)
    print(f"Exit code: {result.exit_code}")

asyncio.run(main())
```

That's ~10 lines. Behind the scenes, flaude creates a Fly.io VM with 2 CPUs and 4GB RAM, clones your repo, runs `claude -p "Find and fix any type errors in src/"`, waits for it to finish, and tears down the machine.

### Stream the output in real time

```python
from flaude import MachineConfig, run_with_logs

async def main():
    config = MachineConfig(
        claude_code_oauth_token="sk-ant-oat-...",
        prompt="Refactor the auth module to use JWT",
        repos=["https://github.com/you/your-repo"],
    )

    async with await run_with_logs("my-flaude-app", config) as stream:
        async for line in stream:
            print(line)

    result = await stream.result()
    print(f"Done: exit={result.exit_code}")
```

### Run many prompts at once

```python
from flaude import ConcurrentExecutor, ExecutionRequest, MachineConfig

async def main():
    executor = ConcurrentExecutor("my-flaude-app", max_concurrency=3)

    requests = [
        ExecutionRequest(
            config=MachineConfig(prompt="Add tests for auth", ...),
            tag="auth-tests",
        ),
        ExecutionRequest(
            config=MachineConfig(prompt="Add tests for billing", ...),
            tag="billing-tests",
        ),
    ]

    batch = await executor.run_batch(requests)
    print(f"{batch.succeeded}/{batch.total} succeeded")
```

The `max_concurrency` parameter controls how many machines run at once. Set it to 0 for unlimited.

## What you can build with flaude

flaude is a primitive. It's the foundation layer. Some things people might build on top:

- **CI integration:** Test fails, flaude spins up Claude Code to analyze and fix it
- **Code review bots:** Run Claude Code against every PR to find issues
- **Multi-agent orchestration:** Spin up N machines that work on different parts of a codebase in parallel
- **Batch processing:** Run the same prompt against hundreds of repos
- **Scheduled maintenance:** Cron job that runs Claude Code against your repo weekly to find tech debt

## Architecture

```
Your code                    Fly.io
-------                      ------
MachineConfig --> create VM --> clone repos
                                  |
                              run Claude Code
                                  |
            <-- stream logs <-- stdout/stderr
                                  |
              destroy VM <---- exit
```

The Docker image (`ghcr.io/ravi-hq/flaude:latest`) contains Node.js 22, Claude Code (npm), git, and the GitHub CLI. The entrypoint script handles repo cloning, credential setup, and Claude Code invocation.

The library talks to Fly.io's Machines API over HTTPS. Log streaming uses Fly.io's HTTP log drain infrastructure, with a custom asyncio HTTP server that receives NDJSON payloads.

## Configuration

Everything is configured through `MachineConfig`:

| Field | Default | What it does |
|-------|---------|--------------|
| `prompt` | *(required)* | What Claude Code should do |
| `repos` | `[]` | Repos to clone before running |
| `region` | `"iad"` | Where to run (Fly.io region) |
| `vm_cpus` | `2` | CPU count |
| `vm_memory_mb` | `4096` | RAM in MB |
| `env` | `{}` | Extra env vars for the machine |

## Error handling

flaude uses three custom exceptions:

- **`MachineExitError`** — Claude Code exited with non-zero. Includes the last 20 log lines in the error message so you can see what went wrong.
- **`FlyAPIError`** — Fly.io API returned an error. Includes status code, method, URL, and error detail.
- **`ImageBuildError`** — Docker build or push failed.

Machine cleanup is guaranteed by `try/finally`. Even if your code raises, the network drops, or the prompt times out, the machine gets destroyed.

## Links

- **PyPI:** https://pypi.org/project/flaude/
- **GitHub:** https://github.com/ravi-hq/flaude
- **Docs:** https://ravi-hq.github.io/flaude
- **Docker image:** `ghcr.io/ravi-hq/flaude:latest`
