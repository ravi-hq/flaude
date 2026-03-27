# Getting Started

## Prerequisites

### Fly.io account and API token

You need a Fly.io account and an API token with sufficient permissions to create apps and
machines. Create an org-scoped token:

```bash
fly tokens create org
```

Export the token so flaude can find it:

```bash
export FLY_API_TOKEN="FlyV1 ..."
```

!!! note
    flaude reads `FLY_API_TOKEN` from the environment automatically. You can also pass
    `token=` explicitly to any function if you prefer to manage credentials in code.

### Claude Code OAuth token

Claude Code running on the Fly machine needs an OAuth token to authenticate with Anthropic.
Obtain one from your Claude Code installation and keep it available as a string in your code
or as an environment variable.

### GitHub PAT (optional, private repos only)

If you want to clone private GitHub repositories, you need a GitHub personal access token
with `repo` scope and your GitHub username. See
[Private Repositories](guide/private-repos.md) for details.

### Docker (optional, custom images only)

If you want to build and push a customized Docker image, you need Docker installed locally.
The pre-built image at `ghcr.io/ravi-hq/flaude:latest` works for most use cases and
requires no local Docker installation. See [Docker Image](guide/docker-image.md) for when
you'd need this.

---

## First run tutorial

### Step 1 — ensure the Fly app exists

flaude needs a Fly.io app to run machines in. `ensure_app` creates the app if it does not
exist, or returns the existing one:

```python
from flaude import ensure_app

app = await ensure_app("my-flaude-app")
print(f"Using app: {app.name}")
```

You only need to call `ensure_app` once — subsequent calls return the existing app. The app
name must be unique within Fly.io.

### Step 2 — configure the machine

`MachineConfig` describes the VM: what prompt to run, which repos to clone, and what
credentials to use:

```python
from flaude import MachineConfig

config = MachineConfig(
    claude_code_oauth_token="sk-ant-oat-...",
    github_username="your-username",
    github_token="ghp_...",
    prompt="List all Python files in the repository and count the total lines of code.",
    repos=["https://github.com/your-org/your-repo"],
)
```

!!! tip
    The `repos` field accepts plain URL strings for the common case. Use `RepoSpec` when
    you need a specific branch or a custom target directory.

### Step 3 — run and destroy

`run_and_destroy` creates the machine, waits for Claude Code to complete, and **always**
destroys the machine when done — regardless of success or failure:

```python
from flaude import run_and_destroy, MachineExitError

try:
    result = await run_and_destroy(app.name, config)
    print(f"Exit code: {result.exit_code}")
    print(f"Machine state: {result.state}")
    print(f"Machine destroyed: {result.destroyed}")
except MachineExitError as exc:
    print(f"Claude Code failed with exit code {exc.exit_code}")
    print("Last log lines:")
    for line in exc.logs[-10:]:
        print(f"  {line}")
```

### Step 4 — print the RunResult

`RunResult` carries the final outcome:

| Field | Type | Description |
|-------|------|-------------|
| `machine_id` | `str` | The Fly machine ID |
| `exit_code` | `int \| None` | Process exit code (0 = success) |
| `state` | `str` | Final machine state (`stopped`, `failed`, `destroyed`) |
| `destroyed` | `bool` | Whether the machine was successfully destroyed |

### Putting it all together

```python
import asyncio
from flaude import MachineConfig, MachineExitError, ensure_app, run_and_destroy

async def run_claude(prompt: str, repo_url: str) -> int:
    app = await ensure_app("my-flaude-app")

    config = MachineConfig(
        claude_code_oauth_token="sk-ant-oat-...",
        github_username="your-username",
        github_token="ghp_...",
        prompt=prompt,
        repos=[repo_url],
    )

    try:
        result = await run_and_destroy(app.name, config)
        print(f"Success — exit code {result.exit_code}")
        return result.exit_code or 0
    except MachineExitError as exc:
        print(f"Failed — exit code {exc.exit_code}, state {exc.state}")
        return exc.exit_code or 1

asyncio.run(run_claude(
    "Summarize the changes in the last 5 commits",
    "https://github.com/your-org/your-repo",
))
```

!!! warning
    Machines cost money while they run. flaude's `try/finally` guarantee means a machine is
    always destroyed when `run_and_destroy` returns — but if your process is killed
    (e.g. SIGKILL), the machine may need manual cleanup in the Fly dashboard.

---

## Next steps

- [Streaming Logs](guide/streaming.md) — watch Claude Code output in real time
- [Concurrent Execution](guide/concurrent.md) — run many prompts in parallel
- [Error Handling](guide/error-handling.md) — handle failures gracefully
