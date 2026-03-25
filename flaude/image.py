"""Build and push the flaude Docker image to Fly.io's container registry.

Uses subprocess calls to ``docker`` and ``flyctl`` CLI tools.
The Dockerfile lives in the ``flaude/`` directory at the project root.

Registry convention: ``registry.fly.io/<app-name>:<tag>``
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Default image tag — callers can override
DEFAULT_TAG = "latest"

# The flaude/ directory containing the Dockerfile, relative to the package
# We resolve it at call time so tests can override via docker_context param.
_PACKAGE_DIR = Path(__file__).resolve().parent
_DEFAULT_DOCKER_CONTEXT = _PACKAGE_DIR.parent.parent / "flaude"


class ImageBuildError(Exception):
    """Raised when the Docker image build or push fails."""

    def __init__(self, message: str, returncode: int | None = None, stderr: str = ""):
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(message)


def _image_ref(app_name: str, tag: str = DEFAULT_TAG) -> str:
    """Return the full registry image reference for a Fly app."""
    return f"registry.fly.io/{app_name}:{tag}"


async def _run_subprocess(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: str | Path | None = None,
    timeout: float = 600,
) -> subprocess.CompletedProcess[str]:
    """Run a command asynchronously and return the result.

    Raises ImageBuildError if the command exits with a non-zero code.
    """
    cmd_str = " ".join(cmd)
    logger.info("Running: %s", cmd_str)

    merged_env = {**os.environ, **(env or {})}

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=merged_env,
        cwd=cwd,
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise ImageBuildError(
            f"Command timed out after {timeout}s: {cmd_str}",
            returncode=-1,
            stderr="timeout",
        )

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")

    if proc.returncode != 0:
        logger.error(
            "Command failed (rc=%d): %s\nstderr: %s", proc.returncode, cmd_str, stderr
        )
        raise ImageBuildError(
            f"Command failed (rc={proc.returncode}): {cmd_str}",
            returncode=proc.returncode,
            stderr=stderr,
        )

    if stdout:
        logger.debug("stdout: %s", stdout[:500])

    return subprocess.CompletedProcess(
        args=cmd, returncode=proc.returncode or 0, stdout=stdout, stderr=stderr
    )


async def docker_build(
    app_name: str,
    *,
    tag: str = DEFAULT_TAG,
    docker_context: Path | None = None,
    timeout: float = 600,
) -> str:
    """Build the flaude Docker image and tag it for Fly's registry.

    Args:
        app_name: The Fly.io app name (used in the image tag).
        tag: Image tag. Defaults to ``latest``.
        docker_context: Path to the directory containing the Dockerfile.
            Defaults to the ``flaude/`` directory in the project root.
        timeout: Max seconds to wait for the build. Defaults to 600 (10 min).

    Returns:
        The full image reference (e.g. ``registry.fly.io/my-app:latest``).

    Raises:
        ImageBuildError: If the docker build command fails.
    """
    context = docker_context or _DEFAULT_DOCKER_CONTEXT
    image = _image_ref(app_name, tag)

    if not Path(context).is_dir():
        raise ImageBuildError(
            f"Docker context directory does not exist: {context}",
            returncode=None,
            stderr="",
        )

    dockerfile = Path(context) / "Dockerfile"
    if not dockerfile.is_file():
        raise ImageBuildError(
            f"Dockerfile not found at: {dockerfile}",
            returncode=None,
            stderr="",
        )

    logger.info("Building Docker image %s from %s", image, context)
    await _run_subprocess(
        ["docker", "build", "-t", image, "."],
        cwd=context,
        timeout=timeout,
    )

    logger.info("Docker image built: %s", image)
    return image


async def docker_login_fly(*, token: str | None = None) -> None:
    """Authenticate Docker to Fly.io's container registry.

    Uses ``flyctl auth docker`` which configures Docker credentials for
    ``registry.fly.io``. Requires FLY_API_TOKEN to be set or passed.

    Args:
        token: Explicit Fly API token. If not provided, uses FLY_API_TOKEN
            from the environment.

    Raises:
        ImageBuildError: If authentication fails.
    """
    env: dict[str, str] = {}
    if token:
        env["FLY_API_TOKEN"] = token

    logger.info("Authenticating Docker with Fly.io registry")
    await _run_subprocess(["flyctl", "auth", "docker"], env=env)
    logger.info("Docker authenticated with registry.fly.io")


async def docker_push(
    app_name: str,
    *,
    tag: str = DEFAULT_TAG,
    timeout: float = 600,
) -> str:
    """Push the flaude Docker image to Fly.io's container registry.

    The image must already be built (via :func:`docker_build`).

    Args:
        app_name: The Fly.io app name.
        tag: Image tag. Defaults to ``latest``.
        timeout: Max seconds to wait for the push. Defaults to 600 (10 min).

    Returns:
        The full image reference that was pushed.

    Raises:
        ImageBuildError: If the docker push command fails.
    """
    image = _image_ref(app_name, tag)

    logger.info("Pushing Docker image %s", image)
    await _run_subprocess(
        ["docker", "push", image],
        timeout=timeout,
    )

    logger.info("Docker image pushed: %s", image)
    return image


async def ensure_image(
    app_name: str,
    *,
    tag: str = DEFAULT_TAG,
    token: str | None = None,
    docker_context: Path | None = None,
    build_timeout: float = 600,
    push_timeout: float = 600,
) -> str:
    """Build, authenticate, and push the flaude Docker image in one call.

    This is the high-level convenience function that orchestrates the full
    image lifecycle: build → login → push.

    Args:
        app_name: The Fly.io app name.
        tag: Image tag. Defaults to ``latest``.
        token: Explicit Fly API token for registry auth.
        docker_context: Override the Docker build context directory.
        build_timeout: Max seconds for the build step.
        push_timeout: Max seconds for the push step.

    Returns:
        The full image reference (e.g. ``registry.fly.io/my-app:latest``).

    Raises:
        ImageBuildError: If any step in the pipeline fails.
    """
    image = await docker_build(
        app_name, tag=tag, docker_context=docker_context, timeout=build_timeout
    )
    await docker_login_fly(token=token)
    await docker_push(app_name, tag=tag, timeout=push_timeout)
    return image
