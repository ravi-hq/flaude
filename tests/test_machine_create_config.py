"""Tests verifying create_machine sends correct env vars and configuration.

These tests exercise the integration between create_machine and
build_machine_config — ensuring the full HTTP payload sent to the Fly.io
Machines API contains the expected env vars, VM sizing, metadata, restart
policy, and other configuration.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx

from flaude.fly_client import FLY_API_BASE
from flaude.machine import create_machine
from flaude.machine_config import (
    DEFAULT_IMAGE,
    DEFAULT_REGION,
    DEFAULT_VM_CPUS,
    DEFAULT_VM_MEMORY_MB,
    MachineConfig,
    RepoSpec,
)

APP = "flaude-test"
TOKEN = "test-fly-token"

MACHINE_RESPONSE = {
    "id": "m_cfg123",
    "name": "cfg-test",
    "state": "created",
    "region": "iad",
    "instance_id": "inst_cfg",
}


def _cfg(**overrides: Any) -> MachineConfig:
    defaults = {
        "claude_code_oauth_token": "oauth-tok",
        "prompt": "Fix the bug",
    }
    defaults.update(overrides)
    return MachineConfig(**defaults)  # type: ignore[arg-type]


def _mock_create() -> Any:
    """Mock the create-machine endpoint and return the route for inspection."""
    return respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        return_value=httpx.Response(200, json=MACHINE_RESPONSE)
    )


async def _get_payload(route: Any) -> dict[str, Any]:
    """Extract the JSON body sent in the first call to the mocked route."""
    return dict(json.loads(route.calls[0].request.content))


# ---------------------------------------------------------------------------
# Required env vars
# ---------------------------------------------------------------------------


@respx.mock
async def test_payload_contains_claude_oauth_token() -> None:
    """CLAUDE_CODE_OAUTH_TOKEN is set from config."""
    route = _mock_create()
    await create_machine(APP, _cfg(claude_code_oauth_token="my-secret"), token=TOKEN)
    body = await _get_payload(route)
    assert body["config"]["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "my-secret"


@respx.mock
async def test_payload_contains_prompt() -> None:
    """FLAUDE_PROMPT is set from the prompt field."""
    route = _mock_create()
    await create_machine(APP, _cfg(prompt="Refactor the module"), token=TOKEN)
    body = await _get_payload(route)
    assert body["config"]["env"]["FLAUDE_PROMPT"] == "Refactor the module"


# ---------------------------------------------------------------------------
# GitHub env vars
# ---------------------------------------------------------------------------


@respx.mock
async def test_payload_contains_github_credentials() -> None:
    """GITHUB_USERNAME and GITHUB_TOKEN appear when set."""
    route = _mock_create()
    cfg = _cfg(github_username="octocat", github_token="ghp_abc123")
    await create_machine(APP, cfg, token=TOKEN)
    body = await _get_payload(route)
    env = body["config"]["env"]
    assert env["GITHUB_USERNAME"] == "octocat"
    assert env["GITHUB_TOKEN"] == "ghp_abc123"


@respx.mock
async def test_payload_omits_github_when_not_set() -> None:
    """GITHUB_USERNAME and GITHUB_TOKEN are absent when not provided."""
    route = _mock_create()
    await create_machine(APP, _cfg(), token=TOKEN)
    body = await _get_payload(route)
    env = body["config"]["env"]
    assert "GITHUB_USERNAME" not in env
    assert "GITHUB_TOKEN" not in env


# ---------------------------------------------------------------------------
# Repos
# ---------------------------------------------------------------------------


@respx.mock
async def test_payload_contains_repos_as_json() -> None:
    """FLAUDE_REPOS contains JSON array of repo specs."""
    route = _mock_create()
    repos = ["https://github.com/a/b", "https://github.com/c/d"]
    await create_machine(APP, _cfg(repos=repos), token=TOKEN)
    body = await _get_payload(route)
    parsed = json.loads(body["config"]["env"]["FLAUDE_REPOS"])
    assert len(parsed) == 2
    assert parsed[0] == {"url": "https://github.com/a/b"}
    assert parsed[1] == {"url": "https://github.com/c/d"}


@respx.mock
async def test_payload_omits_repos_when_empty() -> None:
    """FLAUDE_REPOS is absent when no repos specified."""
    route = _mock_create()
    await create_machine(APP, _cfg(), token=TOKEN)
    body = await _get_payload(route)
    assert "FLAUDE_REPOS" not in body["config"]["env"]


@respx.mock
async def test_payload_single_repo() -> None:
    """Single repo is serialised as JSON array with one entry."""
    route = _mock_create()
    await create_machine(APP, _cfg(repos=["https://github.com/x/y"]), token=TOKEN)
    body = await _get_payload(route)
    parsed = json.loads(body["config"]["env"]["FLAUDE_REPOS"])
    assert len(parsed) == 1
    assert parsed[0] == {"url": "https://github.com/x/y"}


@respx.mock
async def test_payload_repo_spec_with_branch_and_target() -> None:
    """RepoSpec with branch and target_dir appears in JSON payload."""
    route = _mock_create()
    repos = [RepoSpec(url="https://github.com/a/b", branch="main", target_dir="custom")]
    await create_machine(APP, _cfg(repos=repos), token=TOKEN)
    body = await _get_payload(route)
    parsed = json.loads(body["config"]["env"]["FLAUDE_REPOS"])
    assert parsed[0] == {
        "url": "https://github.com/a/b",
        "branch": "main",
        "target_dir": "custom",
    }


# ---------------------------------------------------------------------------
# VM configuration defaults
# ---------------------------------------------------------------------------


@respx.mock
async def test_payload_default_region() -> None:
    """Default region matches DEFAULT_REGION."""
    route = _mock_create()
    await create_machine(APP, _cfg(), token=TOKEN)
    body = await _get_payload(route)
    assert body["region"] == DEFAULT_REGION


@respx.mock
async def test_payload_default_image() -> None:
    """Default image matches DEFAULT_IMAGE."""
    route = _mock_create()
    await create_machine(APP, _cfg(), token=TOKEN)
    body = await _get_payload(route)
    assert body["config"]["image"] == DEFAULT_IMAGE


@respx.mock
async def test_payload_default_vm_sizing() -> None:
    """Default VM sizing uses DEFAULT_VM_CPUS and DEFAULT_VM_MEMORY_MB."""
    route = _mock_create()
    await create_machine(APP, _cfg(), token=TOKEN)
    body = await _get_payload(route)
    guest = body["config"]["guest"]
    assert guest["cpus"] == DEFAULT_VM_CPUS
    assert guest["memory_mb"] == DEFAULT_VM_MEMORY_MB
    assert guest["cpu_kind"] == "performance"


# ---------------------------------------------------------------------------
# Auto-destroy and restart policy
# ---------------------------------------------------------------------------


@respx.mock
async def test_payload_auto_destroy_enabled_by_default() -> None:
    """auto_destroy is True by default."""
    route = _mock_create()
    await create_machine(APP, _cfg(), token=TOKEN)
    body = await _get_payload(route)
    assert body["config"]["auto_destroy"] is True


@respx.mock
async def test_payload_auto_destroy_can_be_disabled() -> None:
    """auto_destroy can be set to False."""
    route = _mock_create()
    await create_machine(APP, _cfg(auto_destroy=False), token=TOKEN)
    body = await _get_payload(route)
    assert body["config"]["auto_destroy"] is False


@respx.mock
async def test_payload_restart_policy_is_no() -> None:
    """Restart policy is 'no' so machines don't restart after completion."""
    route = _mock_create()
    await create_machine(APP, _cfg(), token=TOKEN)
    body = await _get_payload(route)
    assert body["config"]["restart"] == {"policy": "no"}


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


@respx.mock
async def test_payload_metadata_includes_managed_by() -> None:
    """Metadata always includes managed_by=flaude."""
    route = _mock_create()
    await create_machine(APP, _cfg(), token=TOKEN)
    body = await _get_payload(route)
    assert body["config"]["metadata"]["managed_by"] == "flaude"


@respx.mock
async def test_payload_custom_metadata_merged() -> None:
    """Custom metadata is merged alongside managed_by."""
    route = _mock_create()
    cfg = _cfg(metadata={"run_id": "r42", "task": "review"})
    await create_machine(APP, cfg, token=TOKEN)
    body = await _get_payload(route)
    meta = body["config"]["metadata"]
    assert meta["managed_by"] == "flaude"
    assert meta["run_id"] == "r42"
    assert meta["task"] == "review"


# ---------------------------------------------------------------------------
# Custom overrides
# ---------------------------------------------------------------------------


@respx.mock
async def test_payload_custom_region() -> None:
    """Region can be overridden."""
    route = _mock_create()
    await create_machine(APP, _cfg(region="lhr"), token=TOKEN)
    body = await _get_payload(route)
    assert body["region"] == "lhr"


@respx.mock
async def test_payload_custom_vm_sizing() -> None:
    """VM cpus and memory can be overridden."""
    route = _mock_create()
    cfg = _cfg(vm_cpus=4, vm_memory_mb=8192)
    await create_machine(APP, cfg, token=TOKEN)
    body = await _get_payload(route)
    guest = body["config"]["guest"]
    assert guest["cpus"] == 4
    assert guest["memory_mb"] == 8192


@respx.mock
async def test_payload_custom_image() -> None:
    """Custom image overrides the default."""
    route = _mock_create()
    cfg = _cfg(image="registry.fly.io/custom:v2")
    await create_machine(APP, cfg, token=TOKEN)
    body = await _get_payload(route)
    assert body["config"]["image"] == "registry.fly.io/custom:v2"


@respx.mock
async def test_payload_extra_env_vars() -> None:
    """User-supplied env vars are included in the payload."""
    route = _mock_create()
    cfg = _cfg(env={"MY_VAR": "hello", "DEBUG": "1"})
    await create_machine(APP, cfg, token=TOKEN)
    body = await _get_payload(route)
    env = body["config"]["env"]
    assert env["MY_VAR"] == "hello"
    assert env["DEBUG"] == "1"
    # Required vars still present
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-tok"


@respx.mock
async def test_payload_extra_env_can_override_defaults() -> None:
    """User-supplied env vars can override built-in defaults."""
    route = _mock_create()
    cfg = _cfg(env={"FLAUDE_PROMPT": "overridden"})
    await create_machine(APP, cfg, token=TOKEN)
    body = await _get_payload(route)
    assert body["config"]["env"]["FLAUDE_PROMPT"] == "overridden"


# ---------------------------------------------------------------------------
# Machine name
# ---------------------------------------------------------------------------


@respx.mock
async def test_payload_includes_name_when_provided() -> None:
    """Machine name appears at top level of payload when set."""
    route = _mock_create()
    await create_machine(APP, _cfg(), name="my-worker", token=TOKEN)
    body = await _get_payload(route)
    assert body["name"] == "my-worker"


@respx.mock
async def test_payload_omits_name_when_not_provided() -> None:
    """Payload has no 'name' key when name is not given."""
    route = _mock_create()
    await create_machine(APP, _cfg(), token=TOKEN)
    body = await _get_payload(route)
    assert "name" not in body


# ---------------------------------------------------------------------------
# Full integration: all options together
# ---------------------------------------------------------------------------


@respx.mock
async def test_payload_full_config() -> None:
    """A fully-configured machine has all expected fields in the payload."""
    route = _mock_create()
    cfg = MachineConfig(
        image="registry.fly.io/custom:v3",
        claude_code_oauth_token="full-token",
        github_username="user1",
        github_token="ghp_full",
        prompt="Run all tests",
        repos=["https://github.com/org/repo1", "https://github.com/org/repo2"],
        region="cdg",
        vm_cpus=8,
        vm_memory_mb=16384,
        auto_destroy=False,
        env={"EXTRA": "val"},
        metadata={"job_id": "j99"},
    )
    await create_machine(APP, cfg, name="full-test", token=TOKEN)
    body = await _get_payload(route)

    # Top-level
    assert body["name"] == "full-test"
    assert body["region"] == "cdg"

    # Image
    assert body["config"]["image"] == "registry.fly.io/custom:v3"

    # Env vars
    env = body["config"]["env"]
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "full-token"
    assert env["FLAUDE_PROMPT"] == "Run all tests"
    assert env["GITHUB_USERNAME"] == "user1"
    assert env["GITHUB_TOKEN"] == "ghp_full"
    repos_parsed = json.loads(env["FLAUDE_REPOS"])
    assert len(repos_parsed) == 2
    assert repos_parsed[0] == {"url": "https://github.com/org/repo1"}
    assert repos_parsed[1] == {"url": "https://github.com/org/repo2"}
    assert env["EXTRA"] == "val"

    # Guest
    guest = body["config"]["guest"]
    assert guest["cpus"] == 8
    assert guest["memory_mb"] == 16384
    assert guest["cpu_kind"] == "performance"

    # Policies
    assert body["config"]["auto_destroy"] is False
    assert body["config"]["restart"] == {"policy": "no"}

    # Metadata
    meta = body["config"]["metadata"]
    assert meta["managed_by"] == "flaude"
    assert meta["job_id"] == "j99"
