# flaude

[![CI](https://github.com/ravi-hq/flaude/actions/workflows/ci.yml/badge.svg)](https://github.com/ravi-hq/flaude/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/flaude)](https://pypi.org/project/flaude/)
[![Python](https://img.shields.io/pypi/pyversions/flaude)](https://pypi.org/project/flaude/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/ravi-hq/flaude/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-ravi--hq.github.io%2Fflaude-blue)](https://ravi-hq.github.io/flaude)

On-demand Claude Code execution on [Fly.io](https://fly.io) machines.

Spin up ephemeral VMs, run Claude Code prompts against your repos, stream the output back, and auto-destroy the machines when done. No persistent infrastructure required.

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

## Prerequisites

- **Fly.io account** with a valid `FLY_API_TOKEN`
- **Claude Code OAuth token** for authenticating Claude Code on the machine
- **GitHub credentials** (username + PAT) if cloning private repos
- **Docker** (only needed if building/pushing the container image yourself)

## Quick start

### Run a prompt and wait for the result

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

### Stream logs in real time

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

### Run multiple prompts concurrently

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

## API overview

### Configuration

| Class | Purpose |
|-------|---------|
| `MachineConfig` | Full machine configuration: prompt, repos, credentials, VM size, region |
| `RepoSpec` | Repository spec with optional branch and target directory |

### Execution

| Function / Class | Purpose |
|------------------|---------|
| `run_and_destroy()` | Run prompt, wait for exit, destroy machine. Raises on failure. |
| `run()` | Same as above but doesn't raise on non-zero exit. |
| `run_with_logs()` | Run with real-time log streaming via async iterator. |
| `ConcurrentExecutor` | Run multiple prompts in parallel with optional concurrency limits. |

### App & machine management

| Function | Purpose |
|----------|---------|
| `ensure_app()` | Get or create a Fly.io app |
| `create_app()` / `get_app()` | Explicit app create/get |
| `create_machine()` | Create a Fly machine from config |
| `stop_machine()` / `destroy_machine()` | Machine lifecycle control |

### Log infrastructure

| Class / Function | Purpose |
|------------------|---------|
| `LogDrainServer` | HTTP server that receives Fly.io log drain POSTs |
| `LogCollector` | Routes log lines to per-machine async queues |
| `LogStream` | Async iterator over a machine's log output with timeout support |
| `StreamingRun` | Combined async iterator + context manager for streaming executions |
| `fetch_machine_logs()` | Fetch historical logs from Fly platform API (works after machine exits) |

### Image management

| Function | Purpose |
|----------|---------|
| `ensure_image()` | Build and push the Docker image if needed |
| `docker_build()` / `docker_push()` | Explicit build/push |

### Results & errors

| Class | Purpose |
|-------|---------|
| `RunResult` | Exit code, final state, and machine ID |
| `MachineExitError` | Raised on non-zero exit; includes captured log tail |
| `BatchResult` | Aggregated results from concurrent execution |
| `ExecutionResult` | Per-request result within a batch |

## Configuration reference

`MachineConfig` fields:

| Field | Default | Description |
|-------|---------|-------------|
| `image` | `ghcr.io/ravi-hq/flaude:latest` | Docker image |
| `claude_code_oauth_token` | *(required)* | Claude Code auth token |
| `github_username` | `""` | GitHub username for private repos |
| `github_token` | `""` | GitHub PAT for private repos |
| `prompt` | *(required)* | The Claude Code prompt to execute |
| `repos` | `[]` | Repos to clone (URLs or `RepoSpec` objects) |
| `region` | `"iad"` | Fly.io region |
| `vm_size` | `"performance-2x"` | VM preset |
| `vm_cpus` | `2` | vCPUs |
| `vm_memory_mb` | `4096` | RAM in MB |
| `auto_destroy` | `True` | Auto-destroy on exit |
| `env` | `{}` | Additional environment variables |
| `metadata` | `{}` | Machine metadata key-value pairs |

## Environment variables

Set in your local environment:

| Variable | Purpose |
|----------|---------|
| `FLY_API_TOKEN` | Authenticate with the Fly.io Machines API |

Set automatically on the machine by flaude:

| Variable | Purpose |
|----------|---------|
| `CLAUDE_CODE_OAUTH_TOKEN` | Claude Code authentication |
| `GITHUB_USERNAME` | Git credential for repo cloning |
| `GITHUB_TOKEN` | Git credential for repo cloning |
| `FLAUDE_REPOS` | JSON array of repo specs |
| `FLAUDE_PROMPT` | The prompt string |

## Development

```bash
git clone https://github.com/ravi-hq/flaude.git
cd flaude
uv sync --extra dev      # install all dev dependencies
make test                # run unit tests
make check               # lint + type check + security scan
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full contribution guide.

### E2E validation tests

E2E tests spin up real Fly.io machines, run Claude Code, and verify the full lifecycle. They are **excluded by default** — `pytest` alone never runs them.

#### Prerequisites

All required tokens are in `.env`:

| Token | Purpose |
|-------|---------|
| `FLY_API_TOKEN` | Authenticates Fly.io API calls from your machine |
| `CLAUDE_CODE_OAUTH_TOKEN` | Forwarded into the Fly machine for Claude Code auth |
| `GITHUB_USERNAME` | Git clone auth (optional, for private repo tests) |
| `GITHUB_TOKEN` | Git clone auth (optional, for private repo tests) |

Optional:

| Env var | Purpose |
|---------|---------|
| `FLAUDE_E2E_PRIVATE_REPO` | Full URL of a private repo to test cloning |

The Docker image `ghcr.io/ravi-hq/flaude:latest` must be pushed before running E2E tests:

```bash
source .env && python -c "
import asyncio
from flaude import ensure_image
asyncio.run(ensure_image('flaude'))
"
```

The image is built for `linux/amd64` (required by Fly.io) regardless of your host architecture.

#### Running E2E tests

```bash
source .env && pytest -m e2e -v
```

That's it. Each test creates a real Fly machine, runs a prompt, checks the output, and destroys the machine. Expect ~1-3 minutes per test.

#### What the tests validate

| Test | What it proves |
|------|---------------|
| `test_smoke_run_and_destroy` | Full lifecycle works: create machine → run prompt → exit 0 → destroy |
| `test_machine_logs` | Fetches logs via Fly platform API; verifies `[flaude:exit:0]` marker |
| `test_public_repo_clone` | Public GitHub repo clones successfully before Claude Code runs |
| `test_private_repo_clone` | Private repo clone with credentials (skipped if creds absent) |
| `test_machine_cleanup_on_success` | Machine is actually destroyed after run (404 on get) |

#### Running specific tests

```bash
# Just the smoke test (fastest, ~1 min):
source .env && pytest -m e2e -v -k smoke

# Everything including unit tests:
source .env && pytest -m "" -v
```

## License

See repository for license details.
