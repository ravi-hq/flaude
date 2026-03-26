# Architecture

## Overview

flaude is a thin orchestration layer between your Python process and the Fly.io Machines
API. It creates ephemeral VMs on demand, streams their output back, and ensures they are
always destroyed when done.

```
Your process
    │
    │  1. ensure_app / create_app
    │  ─────────────────────────►  Fly.io Apps API
    │
    │  2. run_and_destroy / run_with_logs
    │     a. Start LogDrainServer (local HTTP)
    │     b. POST /v1/apps/{app}/machines
    │  ─────────────────────────►  Fly.io Machines API
    │
    │                              VM boots
    │                              entrypoint.sh runs
    │                              git clone repos → /workspace
    │                              claude -p "..." --print
    │
    │  c. Fly delivers logs via HTTP POST (NDJSON)
    │  ◄─────────────────────────  LogDrainServer
    │     LogCollector routes to machine's asyncio.Queue
    │     LogStream yields lines to your async for loop
    │
    │  d. Machine exits
    │     GET /v1/apps/{app}/machines/{id}/wait
    │  ─────────────────────────►  Fly.io Machines API
    │  ◄─────────────────────────  state=stopped, exit_code=N
    │
    │  e. Cleanup (try/finally)
    │     DELETE /v1/apps/{app}/machines/{id}
    │  ─────────────────────────►  Fly.io Machines API
```

## Module hierarchy

```
flaude/
├── fly_client.py     # Low-level HTTP client for Fly Machines API
│                     # Thin wrapper around httpx with auth and error handling
│
├── app.py            # App CRUD — ensure_app, create_app, get_app
│
├── machine.py        # Machine CRUD — create_machine, stop_machine, destroy_machine
│
├── machine_config.py # MachineConfig dataclass + build_machine_config()
│                     # Translates Python config into Fly API JSON payload
│
├── runner.py         # Core execution lifecycle
│                     # run() / run_and_destroy() — wait + cleanup via try/finally
│                     # MachineExitError, RunResult
│
├── lifecycle.py      # run_with_logs() — log drain integration
│                     # StreamingRun — async iterator + context manager
│
├── log_drain.py      # Log streaming infrastructure
│                     # LogDrainServer — stdlib asyncio HTTP server
│                     # LogCollector  — machine_id → asyncio.Queue routing
│                     # LogStream     — async iterator with timeout support
│
├── executor.py       # Concurrent execution
│                     # ConcurrentExecutor, ExecutionRequest, BatchResult
│
└── image.py          # Docker image build + push
                      # ensure_image, docker_build, docker_push
```

The dependency graph flows strictly downward:
- `executor` → `runner`
- `lifecycle` → `runner`, `log_drain`, `machine`
- `runner` → `machine`, `fly_client`
- `machine` → `fly_client`, `machine_config`
- `app` → `fly_client`

## The try/finally cleanup guarantee

Machine cleanup is always performed in a `try/finally` block, covering:

- Normal completion (exit code 0)
- Failure (non-zero exit code or `failed` state)
- Python exceptions during execution
- `asyncio.CancelledError` (task cancellation)
- `KeyboardInterrupt`

```python
machine = await create_machine(app_name, config)
try:
    state, exit_code = await wait_for_machine_exit(app_name, machine.id)
    return RunResult(...)
finally:
    await _cleanup_machine(app_name, machine.id)  # always runs
```

The cleanup itself is best-effort: it tries `stop_machine` first (graceful), then
`destroy_machine`. If the destroy call fails (e.g. network error, already destroyed),
the error is logged but not re-raised — the original exception propagates normally.

## Why httpx is the only dependency

The Fly.io Machines API is a straightforward REST API over HTTPS. `httpx` provides:

- Async HTTP/1.1 and HTTP/2 support
- Connection pooling across multiple API calls
- Clean `async with` context management

The log drain server does **not** use httpx. It uses the stdlib `asyncio.start_server`
to create a minimal HTTP/1.1 server that handles Fly's NDJSON POST requests. This keeps
the server lightweight and avoids any dependency on ASGI frameworks or extra event loops.

The separation means flaude makes outbound HTTPS calls (Fly API) via httpx and receives
inbound HTTP calls (log drains) via stdlib asyncio — two distinct communication channels
with no shared state.
