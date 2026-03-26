# Building on flaude

This guide covers patterns for building tools and services on top of flaude — things like
CI integrations, automated review pipelines, and multi-tenant execution services.

## Sharing log infrastructure across concurrent machines

When running many machines in parallel with `run_with_logs`, creating a separate
`LogDrainServer` per machine wastes resources. Instead, share a single server and
collector across all machines:

```python
from flaude import (
    LogCollector,
    LogDrainServer,
    MachineConfig,
    run_with_logs,
)
import asyncio

async def run_parallel_with_shared_drain(app_name: str, tasks: list[dict]) -> None:
    # One server and collector shared across all machines
    collector = LogCollector()
    server = LogDrainServer(collector, port=0)  # port=0 = auto-assign
    await server.start()

    try:
        streams = await asyncio.gather(*[
            run_with_logs(
                app_name,
                MachineConfig(
                    claude_code_oauth_token="sk-ant-oat-...",
                    prompt=task["prompt"],
                    repos=task["repos"],
                ),
                collector=collector,  # reuse shared collector
                server=server,        # reuse shared server
            )
            for task in tasks
        ])

        # Consume all streams concurrently
        async def consume(stream, label: str) -> None:
            async with stream:
                async for line in stream:
                    print(f"[{label}] {line}")

        await asyncio.gather(*[
            consume(stream, tasks[i]["label"])
            for i, stream in enumerate(streams)
        ])
    finally:
        await server.stop()
```

!!! note
    When you pass `collector=` and `server=` to `run_with_logs`, the `StreamingRun` does
    not own the server and will not stop it on cleanup. You are responsible for calling
    `server.stop()` when all machines are done.

## Using metadata for tracking

The `metadata` field on `MachineConfig` attaches arbitrary key-value pairs to the Fly
machine. Use it to correlate machines with your own tracking system:

```python
from flaude import MachineConfig, run_and_destroy

async def run_tracked(app_name: str, job_id: str, user_id: str) -> None:
    config = MachineConfig(
        claude_code_oauth_token="sk-ant-oat-...",
        prompt="Generate a comprehensive test suite for the payments module",
        repos=["https://github.com/your-org/your-repo"],
        metadata={
            "job_id": job_id,
            "user_id": user_id,
            "triggered_by": "api",
        },
    )

    result = await run_and_destroy(app_name, config)
    print(f"Job {job_id} completed: exit_code={result.exit_code}")
```

Metadata appears in the Fly dashboard and can be queried via the Machines API, making it
useful for debugging and cleanup of orphaned machines.

## Custom environment variables

Pass extra environment variables to the machine via the `env` field. These are merged with
the variables flaude sets automatically (credentials, repos, prompt):

```python
from flaude import MachineConfig, run_and_destroy

async def run_with_env(app_name: str) -> None:
    config = MachineConfig(
        claude_code_oauth_token="sk-ant-oat-...",
        prompt="Run the integration tests against the staging API",
        repos=["https://github.com/your-org/your-repo"],
        env={
            "API_BASE_URL": "https://staging.api.your-org.com",
            "TEST_TIMEOUT": "120",
        },
    )

    result = await run_and_destroy(app_name, config)
```

!!! warning
    User-supplied `env` values are merged after flaude's required variables. Avoid using
    keys like `CLAUDE_CODE_OAUTH_TOKEN`, `FLAUDE_PROMPT`, or `FLAUDE_REPOS` in `env` —
    they will override flaude's values and break execution.

## CI integration (GitHub Actions)

Run flaude as a step in a GitHub Actions workflow to automate code review or generation
on every pull request:

```yaml
# .github/workflows/claude-review.yml
name: Claude Code Review
on:
  pull_request:
    types: [opened, synchronize]

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install flaude
        run: pip install flaude

      - name: Run Claude review
        env:
          FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}
        run: python scripts/review.py
```

```python
# scripts/review.py
import asyncio
import os
from flaude import MachineConfig, MachineExitError, ensure_app, run_and_destroy

async def main() -> None:
    pr_number = os.environ["PR_NUMBER"]
    repo = os.environ["GITHUB_REPOSITORY"]

    app = await ensure_app("my-flaude-ci")

    config = MachineConfig(
        claude_code_oauth_token=os.environ["CLAUDE_CODE_OAUTH_TOKEN"],
        github_username=os.environ["GITHUB_ACTOR"],
        github_token=os.environ["GITHUB_TOKEN"],
        prompt=(
            f"Review the changes in PR #{pr_number}. "
            "Focus on correctness, security, and test coverage. "
            "Leave a GitHub review comment summarizing your findings."
        ),
        repos=[f"https://github.com/{repo}"],
        metadata={"pr_number": pr_number, "repo": repo},
    )

    try:
        result = await run_and_destroy(app.name, config)
        print(f"Review complete: exit_code={result.exit_code}")
    except MachineExitError as exc:
        print(f"Review failed: {exc}")
        raise SystemExit(1)

asyncio.run(main())
```

## Choosing between ConcurrentExecutor and manual asyncio.gather

**Use `ConcurrentExecutor`** when:
- You have a fixed list of prompts to run in parallel
- You do not need per-machine log streaming
- You want built-in concurrency limiting and `BatchResult` aggregation

```python
executor = ConcurrentExecutor(app_name, max_concurrency=5)
batch = await executor.run_batch(requests)
```

**Use manual `asyncio.gather` with `run_with_logs`** when:
- You need real-time log streaming from each machine simultaneously
- You want to react to individual machine output as it arrives
- You are building a service that routes logs to different destinations per machine

```python
collector = LogCollector()
server = LogDrainServer(collector)
await server.start()

streams = await asyncio.gather(*[
    run_with_logs(app_name, cfg, collector=collector, server=server)
    for cfg in configs
])

# Route each stream to its own handler concurrently
await asyncio.gather(*[handle_stream(s, label) for s, label in zip(streams, labels)])
await server.stop()
```

The manual approach gives you full control at the cost of more boilerplate.
