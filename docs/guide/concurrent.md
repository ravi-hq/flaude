# Concurrent Execution

`ConcurrentExecutor` runs multiple Claude Code prompts in parallel, each on its own
ephemeral Fly machine. All machines are cleaned up regardless of individual success or
failure.

## Basic batch execution

```python
from flaude import ConcurrentExecutor, ExecutionRequest, MachineConfig

async def run_parallel_reviews(app_name: str) -> None:
    executor = ConcurrentExecutor(app_name, max_concurrency=3)

    requests = [
        ExecutionRequest(
            config=MachineConfig(
                claude_code_oauth_token="sk-ant-oat-...",
                prompt="Review the auth module for security issues",
                repos=["https://github.com/your-org/your-repo"],
            ),
            tag="auth-review",
        ),
        ExecutionRequest(
            config=MachineConfig(
                claude_code_oauth_token="sk-ant-oat-...",
                prompt="Review the billing module for security issues",
                repos=["https://github.com/your-org/your-repo"],
            ),
            tag="billing-review",
        ),
        ExecutionRequest(
            config=MachineConfig(
                claude_code_oauth_token="sk-ant-oat-...",
                prompt="Review the API module for security issues",
                repos=["https://github.com/your-org/your-repo"],
            ),
            tag="api-review",
        ),
    ]

    batch = await executor.run_batch(requests)
    print(f"{batch.succeeded}/{batch.total} reviews succeeded")
```

### Tags

The `tag` field on `ExecutionRequest` is a free-form string for correlating results back to
requests. It is returned as-is in `ExecutionResult.tag`. Use it to track which request
produced which result.

## Concurrency limits

`max_concurrency` caps the number of machines running simultaneously. Without a limit all
machines start at once:

```python
# At most 5 machines run at the same time
executor = ConcurrentExecutor(app_name, max_concurrency=5)

# No limit — all machines start immediately
executor = ConcurrentExecutor(app_name)
```

!!! tip
    Fly.io accounts have machine creation rate limits. If you're running hundreds of
    concurrent executions, set a `max_concurrency` to avoid hitting API limits.

## Iterating batch results

`BatchResult.results` is a list of `ExecutionResult` in the same order as the input
requests. Check `.success` and `.error` on each:

```python
batch = await executor.run_batch(requests)

for result in batch.results:
    if result.success:
        print(f"[{result.tag}] OK (exit_code={result.run_result.exit_code})")
    elif result.error is not None:
        print(f"[{result.tag}] ERROR: {result.error}")
    else:
        # Completed but non-zero exit code
        exit_code = result.run_result.exit_code if result.run_result else None
        print(f"[{result.tag}] FAILED (exit_code={exit_code})")
```

### Checking overall success

```python
if batch.all_succeeded:
    print("All tasks completed successfully")
else:
    print(f"{batch.failed} tasks failed out of {batch.total}")
```

## Handling partial failures

`ConcurrentExecutor` never raises an exception from `run_batch` — failures are captured in
`ExecutionResult.error`. This means the batch always completes even if individual machines
fail:

```python
batch = await executor.run_batch(requests)

failed = [r for r in batch.results if not r.success]
if failed:
    for result in failed:
        tag = result.tag
        if result.error:
            print(f"[{tag}] Exception: {result.error}")
        elif result.run_result:
            print(f"[{tag}] Non-zero exit: {result.run_result.exit_code}")
```

!!! warning
    `ExecutionResult.success` is `True` only when `exit_code == 0`. A machine that exits
    with code 1 (e.g. Claude Code encountered an error) is treated as failed even though
    the machine itself ran successfully.

## Single-execution convenience

Use `run_one` when you have a single config but want the `ExecutionResult` wrapper (for
example, to avoid handling `MachineExitError` directly):

```python
result = await executor.run_one(
    config=MachineConfig(
        claude_code_oauth_token="sk-ant-oat-...",
        prompt="Generate a test suite for the payments module",
        repos=["https://github.com/your-org/your-repo"],
    ),
    tag="generate-tests",
)

if result.success:
    print("Tests generated successfully")
else:
    print(f"Generation failed: {result.error}")
```
