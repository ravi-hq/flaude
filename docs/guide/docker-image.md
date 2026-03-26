# Docker Image

## The pre-built image

flaude ships a Docker image pre-configured for Claude Code execution:

```
registry.fly.io/flaude:latest
```

The image includes:

- **Claude Code** — the CLI, pre-installed and ready to run
- **git** — for cloning repositories on machine startup
- **gh CLI** — the GitHub CLI, useful when Claude Code needs to create issues or PRs
- **jq** — JSON processor, useful for scripting
- **entrypoint.sh** — flaude's startup script that configures credentials and clones repos

This image is the default for all `MachineConfig` instances. You do not need Docker
installed locally to use it.

## Building and pushing the image

If you want to push your own copy of the image (for example, to a different Fly app), use
`ensure_image`:

```python
from flaude import ensure_image

async def build_and_push(app_name: str) -> None:
    image = await ensure_image(app_name)
    print(f"Image ready: {image}")
```

`ensure_image` runs three steps in sequence:

1. `docker_build` — builds the image and tags it as `registry.fly.io/<app_name>:latest`
2. `docker_login_fly` — authenticates Docker with Fly's container registry
3. `docker_push` — pushes the image to `registry.fly.io`

### Building and pushing separately

```python
from flaude import docker_build, docker_login_fly, docker_push

async def build_separately(app_name: str) -> None:
    # Build the image
    image = await docker_build(app_name)
    print(f"Built: {image}")

    # Authenticate (requires FLY_API_TOKEN in environment)
    await docker_login_fly()

    # Push to Fly registry
    pushed = await docker_push(app_name)
    print(f"Pushed: {pushed}")
```

!!! note
    `docker_build` and `docker_push` require Docker to be installed and running locally.
    `docker_login_fly` requires `flyctl` to be installed.

## Customizing the image

You would customize the image when you need to:

- Install additional system packages (e.g. `libpq-dev` for PostgreSQL tools)
- Change the Node.js version (Claude Code is Node-based)
- Pre-install project-specific tools or language runtimes to reduce startup time
- Add custom entrypoint logic before repos are cloned

To customize, edit the `Dockerfile` in the `flaude/` directory of the project, then build
and push your customized image:

```python
from pathlib import Path
from flaude import ensure_image

async def push_custom_image(app_name: str) -> None:
    # Point to your customized Dockerfile directory
    image = await ensure_image(
        app_name,
        docker_context=Path("./my-custom-flaude"),
    )
    print(f"Custom image ready: {image}")
```

Then reference your custom image in `MachineConfig`:

```python
from flaude import MachineConfig

config = MachineConfig(
    image=f"registry.fly.io/{app_name}:latest",
    claude_code_oauth_token="sk-ant-oat-...",
    prompt="...",
    repos=["..."],
)
```

!!! tip
    Keep customizations minimal. The pre-built image is kept lean so machines start
    quickly. Adding large packages or runtimes increases VM cold-start time.
