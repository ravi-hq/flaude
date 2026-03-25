"""Tests for flaude.image — Docker image build and push to Fly.io registry."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flaude.image import (
    DEFAULT_TAG,
    ImageBuildError,
    _image_ref,
    _run_subprocess,
    docker_build,
    docker_login_fly,
    docker_push,
    ensure_image,
)


# ---------------------------------------------------------------------------
# _image_ref
# ---------------------------------------------------------------------------


def test_image_ref_default_tag():
    assert _image_ref("my-app") == "registry.fly.io/my-app:latest"


def test_image_ref_custom_tag():
    assert _image_ref("my-app", "v1.2.3") == "registry.fly.io/my-app:v1.2.3"


# ---------------------------------------------------------------------------
# _run_subprocess
# ---------------------------------------------------------------------------


async def test_run_subprocess_success():
    """Successful command returns CompletedProcess with stdout/stderr."""
    result = await _run_subprocess(["echo", "hello"])
    assert result.returncode == 0
    assert "hello" in result.stdout


async def test_run_subprocess_failure():
    """Non-zero exit raises ImageBuildError."""
    with pytest.raises(ImageBuildError) as exc_info:
        await _run_subprocess(["false"])
    assert exc_info.value.returncode != 0


async def test_run_subprocess_timeout():
    """Command exceeding timeout raises ImageBuildError."""
    with pytest.raises(ImageBuildError, match="timed out"):
        await _run_subprocess(["sleep", "60"], timeout=0.1)


# ---------------------------------------------------------------------------
# docker_build
# ---------------------------------------------------------------------------


async def test_docker_build_missing_context(tmp_path: Path):
    """docker_build raises when the context directory doesn't exist."""
    missing = tmp_path / "nonexistent"
    with pytest.raises(ImageBuildError, match="does not exist"):
        await docker_build("my-app", docker_context=missing)


async def test_docker_build_missing_dockerfile(tmp_path: Path):
    """docker_build raises when Dockerfile is missing from context."""
    with pytest.raises(ImageBuildError, match="Dockerfile not found"):
        await docker_build("my-app", docker_context=tmp_path)


@patch("flaude.image._run_subprocess", new_callable=AsyncMock)
async def test_docker_build_calls_docker(mock_run: AsyncMock, tmp_path: Path):
    """docker_build invokes 'docker build' with correct args."""
    # Create a fake Dockerfile so validation passes
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")

    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    result = await docker_build("test-app", tag="v1", docker_context=tmp_path)

    assert result == "registry.fly.io/test-app:v1"
    mock_run.assert_called_once()
    call_args = mock_run.call_args
    cmd = call_args[0][0]
    assert cmd == ["docker", "build", "-t", "registry.fly.io/test-app:v1", "."]
    assert call_args[1]["cwd"] == tmp_path


@patch("flaude.image._run_subprocess", new_callable=AsyncMock)
async def test_docker_build_default_tag(mock_run: AsyncMock, tmp_path: Path):
    """docker_build uses DEFAULT_TAG when none specified."""
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    result = await docker_build("myapp", docker_context=tmp_path)

    assert result == f"registry.fly.io/myapp:{DEFAULT_TAG}"


# ---------------------------------------------------------------------------
# docker_login_fly
# ---------------------------------------------------------------------------


@patch("flaude.image._run_subprocess", new_callable=AsyncMock)
async def test_docker_login_fly_calls_flyctl(mock_run: AsyncMock):
    """docker_login_fly runs 'flyctl auth docker'."""
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    await docker_login_fly(token="tok-123")

    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert cmd == ["flyctl", "auth", "docker"]
    env = mock_run.call_args[1]["env"]
    assert env["FLY_API_TOKEN"] == "tok-123"


@patch("flaude.image._run_subprocess", new_callable=AsyncMock)
async def test_docker_login_fly_no_token(mock_run: AsyncMock):
    """docker_login_fly works without explicit token (uses env)."""
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    await docker_login_fly()

    env = mock_run.call_args[1]["env"]
    # No explicit token set — env dict should be empty
    assert env == {}


# ---------------------------------------------------------------------------
# docker_push
# ---------------------------------------------------------------------------


@patch("flaude.image._run_subprocess", new_callable=AsyncMock)
async def test_docker_push_calls_docker(mock_run: AsyncMock):
    """docker_push runs 'docker push' with the correct image ref."""
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    result = await docker_push("push-app", tag="abc")

    assert result == "registry.fly.io/push-app:abc"
    cmd = mock_run.call_args[0][0]
    assert cmd == ["docker", "push", "registry.fly.io/push-app:abc"]


# ---------------------------------------------------------------------------
# ensure_image
# ---------------------------------------------------------------------------


@patch("flaude.image.docker_push", new_callable=AsyncMock)
@patch("flaude.image.docker_login_fly", new_callable=AsyncMock)
@patch("flaude.image.docker_build", new_callable=AsyncMock)
async def test_ensure_image_full_pipeline(
    mock_build: AsyncMock,
    mock_login: AsyncMock,
    mock_push: AsyncMock,
):
    """ensure_image calls build → login → push in order."""
    mock_build.return_value = "registry.fly.io/e2e-app:latest"
    mock_push.return_value = "registry.fly.io/e2e-app:latest"

    result = await ensure_image("e2e-app", token="tok-abc")

    assert result == "registry.fly.io/e2e-app:latest"
    mock_build.assert_called_once_with(
        "e2e-app", tag=DEFAULT_TAG, docker_context=None, timeout=600
    )
    mock_login.assert_called_once_with(token="tok-abc")
    mock_push.assert_called_once_with("e2e-app", tag=DEFAULT_TAG, timeout=600)


@patch("flaude.image.docker_push", new_callable=AsyncMock)
@patch("flaude.image.docker_login_fly", new_callable=AsyncMock)
@patch("flaude.image.docker_build", new_callable=AsyncMock)
async def test_ensure_image_build_failure_stops_pipeline(
    mock_build: AsyncMock,
    mock_login: AsyncMock,
    mock_push: AsyncMock,
):
    """ensure_image does not login/push if build fails."""
    mock_build.side_effect = ImageBuildError("build broke", returncode=1, stderr="err")

    with pytest.raises(ImageBuildError, match="build broke"):
        await ensure_image("fail-app")

    mock_login.assert_not_called()
    mock_push.assert_not_called()


@patch("flaude.image.docker_push", new_callable=AsyncMock)
@patch("flaude.image.docker_login_fly", new_callable=AsyncMock)
@patch("flaude.image.docker_build", new_callable=AsyncMock)
async def test_ensure_image_custom_params(
    mock_build: AsyncMock,
    mock_login: AsyncMock,
    mock_push: AsyncMock,
    tmp_path: Path,
):
    """ensure_image forwards all parameters correctly."""
    mock_build.return_value = "registry.fly.io/custom:v2"
    mock_push.return_value = "registry.fly.io/custom:v2"

    result = await ensure_image(
        "custom",
        tag="v2",
        token="my-tok",
        docker_context=tmp_path,
        build_timeout=120,
        push_timeout=300,
    )

    assert result == "registry.fly.io/custom:v2"
    mock_build.assert_called_once_with(
        "custom", tag="v2", docker_context=tmp_path, timeout=120
    )
    mock_login.assert_called_once_with(token="my-tok")
    mock_push.assert_called_once_with("custom", tag="v2", timeout=300)
