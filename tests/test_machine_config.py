"""Tests for flaude.machine_config — Fly machine configuration builder."""

from __future__ import annotations

import json

import pytest

from flaude.machine_config import (
    DEFAULT_IMAGE,
    DEFAULT_REGION,
    DEFAULT_VM_CPUS,
    DEFAULT_VM_MEMORY_MB,
    MachineConfig,
    RepoSpec,
    build_machine_config,
)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_build_raises_without_oauth_token():
    """build_machine_config raises ValueError when token is missing."""
    cfg = MachineConfig(prompt="hello")
    with pytest.raises(ValueError, match="claude_code_oauth_token"):
        build_machine_config(cfg)


def test_build_raises_without_prompt():
    """build_machine_config raises ValueError when prompt is missing."""
    cfg = MachineConfig(claude_code_oauth_token="tok")
    with pytest.raises(ValueError, match="prompt"):
        build_machine_config(cfg)


# ---------------------------------------------------------------------------
# Minimal valid config
# ---------------------------------------------------------------------------


def _minimal_config(**overrides) -> MachineConfig:
    defaults = {
        "claude_code_oauth_token": "test-oauth-token",
        "prompt": "Fix the bug",
    }
    defaults.update(overrides)
    return MachineConfig(**defaults)


def test_build_minimal_config_structure():
    """Minimal config produces a well-formed payload."""
    payload = build_machine_config(_minimal_config())

    assert payload["region"] == DEFAULT_REGION
    cfg = payload["config"]
    assert cfg["image"] == DEFAULT_IMAGE
    assert cfg["auto_destroy"] is True
    assert cfg["restart"] == {"policy": "no"}
    assert cfg["guest"]["cpus"] == DEFAULT_VM_CPUS
    assert cfg["guest"]["memory_mb"] == DEFAULT_VM_MEMORY_MB
    assert cfg["guest"]["cpu_kind"] == "performance"


def test_build_sets_required_env_vars():
    """Required env vars are present in the payload."""
    payload = build_machine_config(_minimal_config())
    env = payload["config"]["env"]

    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "test-oauth-token"
    assert env["FLAUDE_PROMPT"] == "Fix the bug"


def test_build_sets_github_env_vars():
    """GitHub credentials appear only when provided."""
    cfg = _minimal_config(github_username="octocat", github_token="ghp_abc")
    payload = build_machine_config(cfg)
    env = payload["config"]["env"]

    assert env["GITHUB_USERNAME"] == "octocat"
    assert env["GITHUB_TOKEN"] == "ghp_abc"


def test_build_omits_github_env_vars_when_empty():
    """GitHub env vars are absent when not provided."""
    payload = build_machine_config(_minimal_config())
    env = payload["config"]["env"]

    assert "GITHUB_USERNAME" not in env
    assert "GITHUB_TOKEN" not in env


# ---------------------------------------------------------------------------
# Repos
# ---------------------------------------------------------------------------


def test_build_serialises_repos_as_json():
    """Multiple repos are serialised as a JSON array in FLAUDE_REPOS."""
    cfg = _minimal_config(
        repos=["https://github.com/a/b", "https://github.com/c/d"]
    )
    payload = build_machine_config(cfg)
    env = payload["config"]["env"]
    parsed = json.loads(env["FLAUDE_REPOS"])

    assert len(parsed) == 2
    assert parsed[0] == {"url": "https://github.com/a/b"}
    assert parsed[1] == {"url": "https://github.com/c/d"}


def test_build_omits_repos_when_empty():
    """FLAUDE_REPOS is absent when repos list is empty."""
    payload = build_machine_config(_minimal_config())
    assert "FLAUDE_REPOS" not in payload["config"]["env"]


# ---------------------------------------------------------------------------
# RepoSpec model
# ---------------------------------------------------------------------------


def test_repo_spec_defaults():
    """RepoSpec has sensible defaults for branch and target_dir."""
    spec = RepoSpec(url="https://github.com/a/b")
    assert spec.url == "https://github.com/a/b"
    assert spec.branch == ""
    assert spec.target_dir == ""


def test_repo_spec_with_branch_and_target():
    """RepoSpec stores branch and target_dir."""
    spec = RepoSpec(url="https://github.com/a/b", branch="main", target_dir="my-repo")
    assert spec.branch == "main"
    assert spec.target_dir == "my-repo"


def test_build_serialises_repo_specs_with_branch():
    """RepoSpec with branch includes branch in JSON."""
    cfg = _minimal_config(
        repos=[RepoSpec(url="https://github.com/a/b", branch="develop")]
    )
    payload = build_machine_config(cfg)
    parsed = json.loads(payload["config"]["env"]["FLAUDE_REPOS"])

    assert len(parsed) == 1
    assert parsed[0] == {"url": "https://github.com/a/b", "branch": "develop"}


def test_build_serialises_repo_specs_with_target_dir():
    """RepoSpec with target_dir includes target_dir in JSON."""
    cfg = _minimal_config(
        repos=[RepoSpec(url="https://github.com/a/b", target_dir="custom")]
    )
    payload = build_machine_config(cfg)
    parsed = json.loads(payload["config"]["env"]["FLAUDE_REPOS"])

    assert parsed[0] == {"url": "https://github.com/a/b", "target_dir": "custom"}


def test_build_serialises_mixed_repos():
    """Mixing plain URL strings and RepoSpec objects works."""
    cfg = _minimal_config(
        repos=[
            "https://github.com/a/b",
            RepoSpec(url="https://github.com/c/d", branch="v2", target_dir="dee"),
        ]
    )
    payload = build_machine_config(cfg)
    parsed = json.loads(payload["config"]["env"]["FLAUDE_REPOS"])

    assert len(parsed) == 2
    assert parsed[0] == {"url": "https://github.com/a/b"}
    assert parsed[1] == {"url": "https://github.com/c/d", "branch": "v2", "target_dir": "dee"}


def test_build_repos_invalid_type_raises():
    """Non-string, non-RepoSpec items in repos raise TypeError."""
    cfg = _minimal_config(repos=[123])  # type: ignore[list-item]
    with pytest.raises(TypeError, match="str or RepoSpec"):
        build_machine_config(cfg)


# ---------------------------------------------------------------------------
# Custom overrides
# ---------------------------------------------------------------------------


def test_build_custom_region_and_size():
    """Region and VM sizing can be overridden."""
    cfg = _minimal_config(region="lhr", vm_cpus=4, vm_memory_mb=8192)
    payload = build_machine_config(cfg)

    assert payload["region"] == "lhr"
    assert payload["config"]["guest"]["cpus"] == 4
    assert payload["config"]["guest"]["memory_mb"] == 8192


def test_build_custom_image():
    """Custom image overrides the default."""
    cfg = _minimal_config(image="registry.fly.io/custom:v2")
    payload = build_machine_config(cfg)
    assert payload["config"]["image"] == "registry.fly.io/custom:v2"


def test_build_extra_env_vars():
    """User-supplied env vars are merged into the payload."""
    cfg = _minimal_config(env={"MY_VAR": "hello"})
    payload = build_machine_config(cfg)
    assert payload["config"]["env"]["MY_VAR"] == "hello"


def test_build_extra_env_can_override_defaults():
    """User-supplied env vars can override built-in ones."""
    cfg = _minimal_config(env={"FLAUDE_PROMPT": "overridden"})
    payload = build_machine_config(cfg)
    assert payload["config"]["env"]["FLAUDE_PROMPT"] == "overridden"


def test_build_metadata_includes_managed_by():
    """Metadata always includes managed_by=flaude."""
    payload = build_machine_config(_minimal_config())
    assert payload["config"]["metadata"]["managed_by"] == "flaude"


def test_build_custom_metadata():
    """Custom metadata is merged with defaults."""
    cfg = _minimal_config(metadata={"run_id": "abc123"})
    payload = build_machine_config(cfg)
    meta = payload["config"]["metadata"]
    assert meta["managed_by"] == "flaude"
    assert meta["run_id"] == "abc123"


def test_build_auto_destroy_false():
    """auto_destroy can be disabled."""
    cfg = _minimal_config(auto_destroy=False)
    payload = build_machine_config(cfg)
    assert payload["config"]["auto_destroy"] is False
