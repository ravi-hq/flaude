"""End-to-end tests that run real Fly.io machines with real tokens.

These tests are excluded from the default test run. To execute them:

    FLY_API_TOKEN="..." CLAUDE_CODE_OAUTH_TOKEN="..." pytest -m e2e -v

Or load from .env:

    source .env && CLAUDE_CODE_OAUTH_TOKEN="..." pytest -m e2e -v

Prerequisites:
    - A valid FLY_API_TOKEN with permission to create machines
    - A valid CLAUDE_CODE_OAUTH_TOKEN for Claude Code auth
    - The Docker image registry.fly.io/flaude:latest must be pushed
    - For private repo tests: GITHUB_USERNAME and GITHUB_TOKEN
"""

from __future__ import annotations

import asyncio
import logging
import os

import pytest

from flaude import (
    FlyApp,
    MachineConfig,
    fetch_machine_logs,
    get_machine,
    run_and_destroy,
)
from flaude.fly_client import FlyAPIError

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.e2e

# Generous timeout — machine boot + Claude Code startup can take a while
E2E_TIMEOUT = 300


def _make_config(
    claude_token: str,
    prompt: str,
    *,
    repos: list[str] | None = None,
    github_username: str = "",
    github_token: str = "",
) -> MachineConfig:
    return MachineConfig(
        claude_code_oauth_token=claude_token,
        prompt=prompt,
        repos=repos or [],
        github_username=github_username,
        github_token=github_token,
    )


# ---------------------------------------------------------------------------
# Test 1: Smoke test — minimal run with no repos
# ---------------------------------------------------------------------------


async def test_smoke_run_and_destroy(
    e2e_app: FlyApp,
    fly_token: str,
    claude_token: str,
) -> None:
    """Validate the full lifecycle: create -> run -> destroy with a trivial prompt."""
    config = _make_config(claude_token, "Print the word PONG")

    async with asyncio.timeout(E2E_TIMEOUT):
        result = await run_and_destroy(
            e2e_app.name,
            config,
            token=fly_token,
            raise_on_failure=False,
        )

    logger.info("Smoke test result: machine=%s exit=%s", result.machine_id, result.exit_code)
    assert result.exit_code == 0, f"Expected exit code 0, got {result.exit_code}"


# ---------------------------------------------------------------------------
# Test 2: Streaming logs — validate log drain works end-to-end
# ---------------------------------------------------------------------------


async def test_machine_logs(
    e2e_app: FlyApp,
    fly_token: str,
    claude_token: str,
) -> None:
    """Validate that machine logs contain expected flaude markers."""
    config = _make_config(claude_token, "Print the word PONG")

    async with asyncio.timeout(E2E_TIMEOUT):
        result = await run_and_destroy(
            e2e_app.name,
            config,
            token=fly_token,
            raise_on_failure=False,
        )

    assert result.exit_code == 0, f"Expected exit code 0, got {result.exit_code}"

    # Fetch logs from the Fly platform logs API (works after machine is destroyed)
    # Small delay to let logs propagate through Fly's log pipeline
    await asyncio.sleep(3)
    logs = await fetch_machine_logs(
        e2e_app.name,
        result.machine_id,
        token=fly_token,
        timeout=30.0,
    )

    log_text = "\n".join(logs)
    logger.info("Log test: %d log lines for machine %s", len(logs), result.machine_id)

    assert any("[flaude] Starting execution" in l for l in logs), (
        f"Expected '[flaude] Starting execution' in logs. Got:\n{log_text[-2000:]}"
    )
    assert any("[flaude:exit:0]" in l for l in logs), (
        f"Expected '[flaude:exit:0]' in logs. Got:\n{log_text[-2000:]}"
    )


# ---------------------------------------------------------------------------
# Test 3: Public repo clone — no GitHub creds needed
# ---------------------------------------------------------------------------


async def test_public_repo_clone(
    e2e_app: FlyApp,
    fly_token: str,
    claude_token: str,
) -> None:
    """Validate cloning a public GitHub repo before running Claude Code."""
    config = _make_config(
        claude_token,
        "List the files in the current directory",
        repos=["https://github.com/octocat/Hello-World"],
    )

    async with asyncio.timeout(E2E_TIMEOUT):
        result = await run_and_destroy(
            e2e_app.name,
            config,
            token=fly_token,
            raise_on_failure=False,
        )

    logger.info("Public repo test: exit=%s", result.exit_code)
    assert result.exit_code == 0, f"Expected exit code 0, got {result.exit_code}"


# ---------------------------------------------------------------------------
# Test 4: Private repo clone — requires GitHub creds
# ---------------------------------------------------------------------------


async def test_private_repo_clone(
    e2e_app: FlyApp,
    fly_token: str,
    claude_token: str,
    github_username: str,
    github_token: str,
) -> None:
    """Validate cloning a private repo with GitHub credentials."""
    private_repo = os.environ.get("FLAUDE_E2E_PRIVATE_REPO", "")
    if not private_repo:
        pytest.skip("FLAUDE_E2E_PRIVATE_REPO not set")
    if not github_username or not github_token:
        pytest.skip("GITHUB_USERNAME and GITHUB_TOKEN required for private repo test")

    config = _make_config(
        claude_token,
        "List the files in the current directory",
        repos=[private_repo],
        github_username=github_username,
        github_token=github_token,
    )

    async with asyncio.timeout(E2E_TIMEOUT):
        result = await run_and_destroy(
            e2e_app.name,
            config,
            token=fly_token,
            raise_on_failure=False,
        )

    assert result.exit_code == 0, f"Expected exit code 0, got {result.exit_code}"


# ---------------------------------------------------------------------------
# Test 5: Machine cleanup verification — confirm machine is destroyed
# ---------------------------------------------------------------------------


async def test_machine_cleanup_on_success(
    e2e_app: FlyApp,
    fly_token: str,
    claude_token: str,
) -> None:
    """Verify that run_and_destroy actually destroys the machine."""
    config = _make_config(claude_token, "Print the word PONG")

    async with asyncio.timeout(E2E_TIMEOUT):
        result = await run_and_destroy(
            e2e_app.name,
            config,
            token=fly_token,
            raise_on_failure=False,
        )

    assert result.exit_code == 0

    # The machine should be gone — get_machine should 404.
    # Fly may take a moment to fully remove the machine record, so retry briefly.
    machine_gone = False
    for _ in range(5):
        try:
            m = await get_machine(e2e_app.name, result.machine_id, token=fly_token)
            # Machine still visible — check if it's in a destroyed state
            if m.state in ("destroyed", "destroying"):
                machine_gone = True
                break
            await asyncio.sleep(2)
        except FlyAPIError as exc:
            if exc.status_code == 404:
                machine_gone = True
                break
            raise

    assert machine_gone, (
        f"Expected machine {result.machine_id} to be destroyed or 404"
    )
