"""Fly.io machine configuration builder for flaude.

Builds the JSON payload required by the Fly.io Machines API to launch
a machine that runs Claude Code against a set of repositories.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Sensible defaults for Claude Code workloads
DEFAULT_VM_SIZE = "performance-2x"  # 2 shared CPUs, 4GB RAM
DEFAULT_VM_CPUS = 2
DEFAULT_VM_MEMORY_MB = 4096
DEFAULT_REGION = "iad"
DEFAULT_IMAGE = "ghcr.io/ravi-hq/flaude:latest"
DEFAULT_AUTO_DESTROY = True


@dataclass
class RepoSpec:
    """Specification for a repository to clone on machine startup.

    Attributes:
        url: Repository URL (e.g. ``https://github.com/org/repo``).
        branch: Optional branch/tag/ref to check out after cloning.
            Defaults to the repository's default branch when empty.
        target_dir: Optional target directory name under ``/workspace``.
            Defaults to the repo name derived from the URL when empty.
    """

    url: str
    branch: str = ""
    target_dir: str = ""


def _normalise_repos(repos: list[str | RepoSpec]) -> list[RepoSpec]:
    """Convert a mixed list of strings and RepoSpec objects to RepoSpec list."""
    result: list[RepoSpec] = []
    for item in repos:
        if isinstance(item, str):
            result.append(RepoSpec(url=item))
        elif isinstance(item, RepoSpec):
            result.append(item)
        else:
            raise TypeError(
                f"repos items must be str or RepoSpec, got {type(item).__name__}"
            )
    return result


def _serialise_repos(repos: list[RepoSpec]) -> str:
    """Serialise a list of RepoSpec to a JSON string for the FLAUDE_REPOS env var."""
    specs = []
    for r in repos:
        spec: dict[str, str] = {"url": r.url}
        if r.branch:
            spec["branch"] = r.branch
        if r.target_dir:
            spec["target_dir"] = r.target_dir
        specs.append(spec)
    return json.dumps(specs)


@dataclass
class MachineConfig:
    """Configuration for a flaude Fly.io machine.

    Attributes:
        image: Docker image reference for the machine.
        claude_code_oauth_token: OAuth token for Claude Code authentication.
        github_username: GitHub username for repo cloning.
        github_token: GitHub personal access token for repo cloning.
        prompt: The Claude Code prompt to execute.
        repos: List of repositories to clone before running the prompt.
            Each entry can be a plain URL string or a :class:`RepoSpec`
            with optional branch and target directory.
        region: Fly.io region to launch in.
        vm_size: Fly.io VM preset size (e.g. ``performance-2x``).
        vm_cpus: Number of vCPUs.
        vm_memory_mb: Memory in megabytes.
        auto_destroy: Whether to auto-destroy the machine when it exits.
        env: Additional environment variables to set on the machine.
        metadata: Arbitrary key-value metadata attached to the machine.
    """

    image: str = DEFAULT_IMAGE
    claude_code_oauth_token: str = ""
    github_username: str = ""
    github_token: str = ""
    prompt: str = ""
    repos: list[str | RepoSpec] = field(default_factory=list)
    region: str = DEFAULT_REGION
    vm_size: str = DEFAULT_VM_SIZE
    vm_cpus: int = DEFAULT_VM_CPUS
    vm_memory_mb: int = DEFAULT_VM_MEMORY_MB
    auto_destroy: bool = DEFAULT_AUTO_DESTROY
    env: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)


def build_machine_config(config: MachineConfig) -> dict[str, Any]:
    """Build the Fly.io Machines API create-machine payload.

    Args:
        config: A :class:`MachineConfig` describing the desired machine.

    Returns:
        A dict suitable for JSON-serialising and POSTing to
        ``POST /v1/apps/{app}/machines``.

    Raises:
        ValueError: If required fields are missing.
    """
    if not config.claude_code_oauth_token:
        raise ValueError("claude_code_oauth_token is required")
    if not config.prompt:
        raise ValueError("prompt is required")

    # Build environment variables — required ones first, then user overrides
    env_vars: dict[str, str] = {
        "CLAUDE_CODE_OAUTH_TOKEN": config.claude_code_oauth_token,
        "FLAUDE_PROMPT": config.prompt,
    }

    if config.github_username:
        env_vars["GITHUB_USERNAME"] = config.github_username
    if config.github_token:
        env_vars["GITHUB_TOKEN"] = config.github_token

    # Repos serialised as JSON array of {url, branch?, target_dir?}
    if config.repos:
        normalised = _normalise_repos(config.repos)
        env_vars["FLAUDE_REPOS"] = _serialise_repos(normalised)

    # Merge user-supplied env vars (they can override defaults if needed)
    env_vars.update(config.env)

    # Machine metadata — useful for tracking / cleanup
    metadata: dict[str, str] = {
        "managed_by": "flaude",
    }
    metadata.update(config.metadata)

    payload: dict[str, Any] = {
        "region": config.region,
        "config": {
            "image": config.image,
            "env": env_vars,
            "guest": {
                "cpu_kind": "performance",
                "cpus": config.vm_cpus,
                "memory_mb": config.vm_memory_mb,
            },
            "auto_destroy": config.auto_destroy,
            "restart": {
                "policy": "no",
            },
            "metadata": metadata,
        },
    }

    return payload
