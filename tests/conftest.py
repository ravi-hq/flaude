"""Shared fixtures for flaude tests.

E2E fixtures read tokens from environment variables and skip tests
when required credentials are not available.
"""

from __future__ import annotations

import os

import pytest

from flaude import FlyApp, ensure_app

# -- E2E credential fixtures (session-scoped, skip if absent) ----------------


@pytest.fixture(scope="session")
def fly_token() -> str:
    token = os.environ.get("FLY_API_TOKEN", "")
    if not token:
        pytest.skip("FLY_API_TOKEN not set")
    return token


@pytest.fixture(scope="session")
def claude_token() -> str:
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    if not token:
        pytest.skip("CLAUDE_CODE_OAUTH_TOKEN not set")
    return token


@pytest.fixture(scope="session")
def github_username() -> str:
    return os.environ.get("GITHUB_USERNAME", "")


@pytest.fixture(scope="session")
def github_token() -> str:
    return os.environ.get("GITHUB_TOKEN", "")


# -- E2E app fixture (session-scoped, reused across all E2E tests) -----------


@pytest.fixture(scope="session")
def e2e_app_name() -> str:
    return os.environ.get("FLAUDE_E2E_APP", "flaude-e2e")


@pytest.fixture(scope="session")
async def e2e_app(e2e_app_name: str, fly_token: str) -> FlyApp:
    return await ensure_app(e2e_app_name, token=fly_token)
