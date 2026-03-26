# Error Handling

flaude raises specific exception types for different failure modes. Understanding them
helps you handle failures precisely and avoid masking bugs.

## MachineExitError

`MachineExitError` is raised when a Fly machine exits with a non-zero exit code or reaches
the `failed` state (OOM kill, entrypoint crash, etc.). It carries the captured log tail so
you can diagnose failures without a separate log retrieval step.

```python
from flaude import MachineConfig, MachineExitError, run_and_destroy

async def run_with_error_handling(app_name: str) -> None:
    config = MachineConfig(
        claude_code_oauth_token="sk-ant-oat-...",
        prompt="Run the full test suite and fix any failures",
        repos=["https://github.com/your-org/your-repo"],
    )

    try:
        result = await run_and_destroy(app_name, config)
    except MachineExitError as exc:
        print(f"Machine {exc.machine_id} failed")
        print(f"  exit_code: {exc.exit_code}")
        print(f"  state:     {exc.state}")
        print(f"  log lines: {len(exc.logs)}")
        print("Last 5 lines:")
        for line in exc.logs[-5:]:
            print(f"  {line}")
```

### Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `machine_id` | `str` | The Fly machine ID |
| `exit_code` | `int \| None` | Process exit code (non-zero, or None if unavailable) |
| `state` | `str` | Final machine state (`stopped`, `failed`) |
| `logs` | `list[str]` | Captured log lines — may be empty if no log drain was configured |

The exception message includes the last 20 log lines for quick debugging when printed or
logged.

### raise_on_failure=False

Both `run_and_destroy` and `StreamingRun.result()` accept `raise_on_failure=False` to
suppress `MachineExitError` and return `RunResult` directly:

```python
result = await run_and_destroy(app_name, config, raise_on_failure=False)
if result.exit_code != 0 or result.state == "failed":
    print(f"Run failed: exit_code={result.exit_code}, state={result.state}")
```

This is useful when you want to inspect the result yourself rather than catching an
exception.

## FlyAPIError

`FlyAPIError` is raised when the Fly.io Machines API returns an error response — for
example, when machine creation fails due to invalid configuration or resource limits:

```python
from flaude.fly_client import FlyAPIError

try:
    result = await run_and_destroy(app_name, config)
except FlyAPIError as exc:
    print(f"Fly API error: {exc.status_code}")
    print(f"Detail: {exc.detail}")
```

| Attribute | Type | Description |
|-----------|------|-------------|
| `status_code` | `int` | HTTP status code from the Fly API |
| `detail` | `str` | Error message from the API response |

!!! tip
    A 404 error during `wait_for_machine_exit` is treated as a successful `destroyed` state
    — flaude handles this transparently since Fly auto-destroys machines when `auto_destroy=True`.

## ImageBuildError

`ImageBuildError` is raised when `docker_build`, `docker_push`, or `ensure_image` fails:

```python
from flaude import ImageBuildError, ensure_image

try:
    image = await ensure_image("my-flaude-app")
except ImageBuildError as exc:
    print(f"Build failed (rc={exc.returncode})")
    print(f"stderr: {exc.stderr}")
```

| Attribute | Type | Description |
|-----------|------|-------------|
| `returncode` | `int \| None` | Exit code of the failed `docker` command |
| `stderr` | `str` | Captured stderr output from the failed command |

## asyncio.TimeoutError

`asyncio.TimeoutError` is raised when a machine does not exit within `wait_timeout`
(default: 3600 seconds). This can happen if Claude Code hangs or the machine gets stuck:

```python
import asyncio
from flaude import run_and_destroy, MachineConfig

try:
    result = await run_and_destroy(app_name, config, wait_timeout=600.0)
except asyncio.TimeoutError:
    print("Machine did not exit within 600 seconds")
    # Note: the machine may still be running on Fly.io — clean it up manually
    # or rely on Fly's own machine lifecycle management.
```

!!! warning
    When a `TimeoutError` is raised from `run_and_destroy`, flaude still attempts cleanup
    in the `finally` block. However, if the machine is stuck, the stop/destroy calls may
    also fail. Check the Fly dashboard if you see orphaned machines.

## Exit code fallback chain

The Fly Machines API does not always populate the exit code in its response — this can
happen when machines are force-destroyed or reach the `failed` state without a clean
process exit.

When the API returns `None` for the exit code, flaude falls back to scanning the collected
log lines for a `[flaude:exit:N]` marker written by the container's `entrypoint.sh` before
the Claude Code process exits:

```
[flaude:exit:0]   # success
[flaude:exit:1]   # Claude Code returned non-zero
```

The fallback chain is:

1. Fly Machines API `events[].status.exit_code`
2. Fly Machines API `status.exit_code`
3. `[flaude:exit:N]` log marker (via `extract_exit_code_from_logs`)
4. `None` if none of the above is available
