# Streaming Logs

`run_with_logs` lets you watch Claude Code output line by line as it arrives, rather than
waiting for the entire execution to complete. It returns a `StreamingRun` that works as
both an async iterator and an async context manager.

## Basic usage

```python
from flaude import MachineConfig, run_with_logs

async def stream_claude(app_name: str) -> None:
    config = MachineConfig(
        claude_code_oauth_token="sk-ant-oat-...",
        prompt="Refactor the auth module to use JWT tokens",
        repos=["https://github.com/your-org/your-repo"],
    )

    async with await run_with_logs(app_name, config) as stream:
        async for line in stream:
            print(line)

    result = await stream.result()
    print(f"Done: exit={result.exit_code}, state={result.state}")
```

The `async with` block guarantees machine cleanup when the block exits — even if an
exception is raised during iteration.

!!! note
    `await run_with_logs(...)` returns the `StreamingRun` object. The `async with` wraps
    the already-created object. This is why the pattern is `async with await run_with_logs(...)`.

## Timeouts

### Per-line timeout

`item_timeout` sets the maximum number of seconds to wait for each individual log line.
If no line arrives within that window, iteration stops silently:

```python
async with await run_with_logs(
    app_name, config, item_timeout=30.0
) as stream:
    async for line in stream:
        print(line)
```

!!! warning
    A per-line timeout that is too short can cut off long-running Claude Code tasks that
    produce infrequent output. Use `total_timeout` if you want a hard wall-clock limit.

### Total timeout

`total_timeout` caps the total time spent iterating the stream, regardless of per-line
activity:

```python
async with await run_with_logs(
    app_name, config, total_timeout=3600.0
) as stream:
    async for line in stream:
        print(line)
```

Both can be combined — whichever limit is hit first stops iteration.

## Collecting all logs

If you want all log lines as a list after the stream completes, use `stream.collected_logs`:

```python
async with await run_with_logs(app_name, config) as stream:
    async for line in stream:
        pass  # or process each line in real time

all_logs = stream.collected_logs
print(f"Total lines: {len(all_logs)}")
```

`collected_logs` accumulates every line yielded by the async iterator. It is only populated
for lines you have actually iterated over — if you stop iteration early, you get a partial
list.

Alternatively, use `LogStream.collect()` directly:

```python
async with await run_with_logs(app_name, config) as stream:
    all_lines = await stream.log_stream.collect()
```

## Getting the result

Call `stream.result()` after iteration to get the `RunResult`. By default it raises
`MachineExitError` on non-zero exits:

```python
from flaude import MachineExitError

async with await run_with_logs(app_name, config) as stream:
    async for line in stream:
        print(line)

try:
    result = await stream.result()
    print(f"Exit code: {result.exit_code}")
except MachineExitError as exc:
    print(f"Failed: exit_code={exc.exit_code}, state={exc.state}")
    print("Captured logs:")
    for line in exc.logs:
        print(f"  {line}")
```

Pass `raise_on_failure=False` to get the `RunResult` without an exception, even on failure:

```python
result = await stream.result(raise_on_failure=False)
if result.exit_code != 0:
    print(f"Non-zero exit: {result.exit_code}")
```

!!! tip
    When using `raise_on_failure=False`, check `result.state` as well as `result.exit_code`.
    A machine that reaches the `failed` state (OOM kill, entrypoint crash) may have
    `exit_code=None`.

## API reference

See [LogStream](../api/log-infrastructure.md) and [StreamingRun](../api/execution.md) for the full
API.
