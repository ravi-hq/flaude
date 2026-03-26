# Private Repositories

flaude can clone private GitHub repositories by passing a GitHub username and personal
access token through `MachineConfig`. The credentials are injected as environment variables
and picked up by `entrypoint.sh`, which configures git credential storage before cloning.

## Basic private repo access

```python
from flaude import MachineConfig, run_and_destroy

async def run_on_private_repo(app_name: str) -> None:
    config = MachineConfig(
        claude_code_oauth_token="sk-ant-oat-...",
        github_username="your-github-username",
        github_token="ghp_...",
        prompt="Audit all uses of raw SQL queries for injection vulnerabilities",
        repos=["https://github.com/your-org/private-backend"],
    )

    result = await run_and_destroy(app_name, config)
    print(f"Done: {result.exit_code}")
```

The `github_username` and `github_token` fields configure HTTPS-based git authentication.
The token needs at least `repo` scope to clone private repositories.

!!! warning
    Never hard-code credentials in source files. Load them from environment variables or a
    secrets manager at runtime.

## RepoSpec — branch and target directory

Use `RepoSpec` instead of a plain URL string when you need to check out a specific branch
or clone into a custom directory under `/workspace`:

```python
from flaude import MachineConfig, RepoSpec, run_and_destroy

async def run_on_feature_branch(app_name: str) -> None:
    config = MachineConfig(
        claude_code_oauth_token="sk-ant-oat-...",
        github_username="your-github-username",
        github_token="ghp_...",
        prompt="Review the changes on this branch for correctness",
        repos=[
            RepoSpec(
                url="https://github.com/your-org/private-backend",
                branch="feature/new-auth",
                target_dir="backend",
            )
        ],
    )

    result = await run_and_destroy(app_name, config)
```

| Field | Default | Description |
|-------|---------|-------------|
| `url` | *(required)* | Repository HTTPS URL |
| `branch` | `""` | Branch, tag, or ref to check out. Defaults to the repo's default branch. |
| `target_dir` | `""` | Directory name under `/workspace`. Defaults to the repo name from the URL. |

## Multiple repositories

`repos` accepts a mixed list of plain URL strings and `RepoSpec` objects:

```python
from flaude import MachineConfig, RepoSpec, run_and_destroy

async def run_multi_repo(app_name: str) -> None:
    config = MachineConfig(
        claude_code_oauth_token="sk-ant-oat-...",
        github_username="your-github-username",
        github_token="ghp_...",
        prompt=(
            "Review the API contract between the backend and frontend. "
            "Check that all API endpoints documented in backend/openapi.yaml "
            "are correctly called in frontend/src/api/."
        ),
        repos=[
            RepoSpec(
                url="https://github.com/your-org/backend",
                branch="main",
                target_dir="backend",
            ),
            "https://github.com/your-org/frontend",
        ],
    )

    result = await run_and_destroy(app_name, config)
```

All repos are cloned in parallel during machine startup. Each is placed under `/workspace/`
using the `target_dir` (or the repo name derived from the URL).

!!! note
    A single `github_token` is used for all repositories in the `repos` list. If your
    repositories live across multiple GitHub organizations with different access tokens, you
    will need separate executions.
