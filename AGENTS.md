# AGENTS.md — AI-optimized reference for flaude

## Identity

- **Package name**: `flaude`
- **Purpose**: Python library for executing Claude Code prompts on ephemeral Fly.io machines
- **Language**: Python 3.11+
- **Single dependency**: httpx (async HTTP)
- **No CLI** — library-only, all async

## Architecture

```
flaude/
├── __init__.py          # Public API (re-exports everything)
├── app.py               # Fly.io app CRUD: ensure_app, create_app, get_app
├── runner.py            # Core execution: run, run_and_destroy, wait_for_machine_exit
├── executor.py          # Concurrent batch: ConcurrentExecutor, run_batch, run_one
├── lifecycle.py         # Log-streaming execution: run_with_logs → StreamingRun
├── machine.py           # Machine CRUD: create_machine, stop_machine, destroy_machine
├── machine_config.py    # Config dataclasses: MachineConfig, RepoSpec, build_machine_config
├── fly_client.py        # Low-level HTTP: fly_get, fly_post, fly_delete, fetch_machine_logs, FlyAPIError
├── log_drain.py         # Log infrastructure: LogDrainServer, LogCollector, LogStream
├── image.py             # Docker: docker_build, docker_push, ensure_image
├── Dockerfile           # Container: Node.js 22 + Claude Code + git + gh CLI
└── entrypoint.sh        # Container startup: clone repos → run claude -p → write exit marker
```

## Execution modes

### 1. Fire-and-forget (`run_and_destroy`)
Create machine → wait for exit → destroy. Returns `RunResult`. Raises `MachineExitError` on non-zero exit.

### 2. Streaming (`run_with_logs`)
Creates `LogDrainServer` before machine → subscribes to logs → returns `StreamingRun` (async iterator + context manager). Background task waits for exit, signals collector, destroys machine.

### 3. Concurrent (`ConcurrentExecutor.run_batch`)
Dispatches multiple `ExecutionRequest` objects via `asyncio.gather` with optional semaphore. Returns `BatchResult` with per-request `ExecutionResult`.

## Key types

```python
# Configuration
MachineConfig(image, claude_code_oauth_token, github_username, github_token, prompt, repos, region, vm_size, vm_cpus, vm_memory_mb, auto_destroy, env, metadata)
RepoSpec(url, branch="", target_dir="")

# Results
RunResult(machine_id, exit_code, state, destroyed)        # frozen dataclass
MachineExitError(machine_id, exit_code, state, logs)      # exception
BatchResult(results, total, succeeded, failed)             # frozen dataclass
ExecutionResult(tag, run_result, error)                    # .success property

# Execution
ExecutionRequest(config, name=None, tag="")
ConcurrentExecutor(app_name, token=None, max_concurrency=None, wait_timeout=3600.0)
StreamingRun  # async iterator + context manager, .result(), .cleanup(), .machine_id

# Infrastructure
FlyApp(name, org, region)
FlyMachine(id, name, state, region, instance_id, app_name)
LogDrainServer(collector, host="0.0.0.0", port=0, include_stderr=False)
LogCollector()  # machine_id → asyncio.Queue routing
LogStream(queue, item_timeout=None, total_timeout=None)    # async iterator
```

## Critical invariants

1. **Guaranteed cleanup**: Every machine is destroyed in a `try/finally` block — exceptions, cancellations, and timeouts all trigger destruction.
2. **Log drain before machine**: `LogDrainServer` starts before `create_machine` so no early log lines are lost.
3. **Exit code fallback**: If Fly API doesn't report exit code, `entrypoint.sh` writes `[flaude:exit:N]` marker parsed by `extract_exit_code_from_logs`.
4. **Never raises in batch**: `ConcurrentExecutor._execute_one` catches all exceptions into `ExecutionResult.error`.
5. **Restart policy is "no"**: Machines run once and stop. `auto_destroy=True` by default.

## Container image

- **Base**: `node:22-bookworm-slim`
- **Pre-installed**: git, jq, gh (GitHub CLI), `@anthropic-ai/claude-code` (npm global)
- **Entrypoint flow**: validate `CLAUDE_CODE_OAUTH_TOKEN` → configure git credentials → clone repos from `FLAUDE_REPOS` JSON → `cd` to workspace → `claude -p -- "$FLAUDE_PROMPT"` → write exit marker → exit

## Environment variables

**Host-side** (your process):
- `FLY_API_TOKEN` — Fly.io API authentication

**Machine-side** (set automatically by `build_machine_config`):
- `CLAUDE_CODE_OAUTH_TOKEN` — Claude Code auth
- `FLAUDE_PROMPT` — The prompt string
- `FLAUDE_REPOS` — JSON array of `{url, branch?, target_dir?}`
- `GITHUB_USERNAME` / `GITHUB_TOKEN` — Git credentials (optional)

## Fly.io API interaction

All API calls go through `fly_client.py` which wraps httpx:
- **Machines API**: `https://api.machines.dev/v1` — auth via `Bearer {FLY_API_TOKEN}`
- **Platform API**: `https://api.fly.io` — auth via raw token as `Authorization` header (no `Bearer` prefix)
- Machine wait: `GET /apps/{app}/machines/{id}/wait?state=stopped` (long-poll), with polling fallback
- Log retrieval: `GET https://api.fly.io/api/v1/apps/{app}/logs?instance={machine_id}` — historical logs (~15 day retention), works after machine exit/destroy
- Terminal states: `stopped`, `destroyed`, `failed`
- Exit code extraction: `event["request"]["exit_event"]["exit_code"]` (or via `monitor_event` wrapper)

## Testing

- **Framework**: pytest + pytest-asyncio (auto mode)
- **HTTP mocking**: respx
- **18 test files** covering: app, machine, config, runner, executor, lifecycle, log drain, log stream, log parsing, entrypoint, exit codes, cleanup guarantees, concurrent integration, failure logs, image, startup env, prompt execution
- **Run unit tests**: `pytest` (E2E excluded by default via `addopts = "-m 'not e2e'"`)
- **Run E2E tests**: `source .env && pytest -m e2e -v`
- **E2E requires**: `FLY_API_TOKEN` + `CLAUDE_CODE_OAUTH_TOKEN` (both in `.env`) + Docker image pushed
- **E2E test files**: `tests/conftest.py` (fixtures), `tests/test_e2e.py` (5 tests)
- **E2E validates**: machine lifecycle, log retrieval via platform API, public/private repo clone, cleanup guarantee
- **Docker image**: Built with `--platform linux/amd64` (required by Fly.io, even on ARM hosts)

## Default machine spec

- VM size: `performance-2x` (2 shared CPUs, 4 GB RAM)
- Region: `iad` (US East)
- Image: `registry.fly.io/flaude:latest`
