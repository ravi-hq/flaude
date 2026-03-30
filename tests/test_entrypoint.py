"""Tests for flaude entrypoint.sh — repo cloning and Claude Code invocation.

These tests validate the entrypoint script behavior by running it with
mock git/claude commands and verifying the expected actions.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest

# Path to the entrypoint script
ENTRYPOINT = Path(__file__).parent.parent / "flaude" / "entrypoint.sh"


@pytest.fixture
def mock_env(tmp_path: Path) -> dict[str, Any]:
    """Create a mock environment for testing the entrypoint script.

    Sets up:
    - A fake workspace directory
    - A mock `git` that logs clone commands to a file
    - A mock `claude` that logs invocations
    - A mock `jq` passthrough (uses real jq)
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    log_file = tmp_path / "commands.log"

    # Mock git: logs clone commands and creates target dir
    mock_git = bin_dir / "git"
    mock_git.write_text(
        textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "git $@" >> {log_file}
        # For clone commands, create the target directory
        if [ "$1" = "clone" ]; then
            # Last argument is the target path
            target="${{@: -1}}"
            mkdir -p "$target"
            echo "cloned" > "$target/.git"
        fi
        exit 0
    """)
    )
    mock_git.chmod(mock_git.stat().st_mode | stat.S_IEXEC)

    # Mock claude: logs the invocation
    mock_claude = bin_dir / "claude"
    mock_claude.write_text(
        textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "claude $@" >> {log_file}
        echo "Claude output for: $2"
        exit 0
    """)
    )
    mock_claude.chmod(mock_claude.stat().st_mode | stat.S_IEXEC)

    # Build env dict
    env = {
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "HOME": str(tmp_path / "home"),
        "FLAUDE_PROMPT": "Fix the tests",
        "CLAUDE_CODE_OAUTH_TOKEN": "test-oauth-token",
    }

    # Create home directory for git config
    (tmp_path / "home").mkdir()

    return {
        "workspace": workspace,
        "bin_dir": bin_dir,
        "log_file": log_file,
        "env": env,
        "tmp_path": tmp_path,
    }


def _run_entrypoint(
    mock_env: dict[str, Any],
    extra_env: dict[str, str] | None = None,
    expect_fail: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run the entrypoint script with the mock environment."""
    env = dict(mock_env["env"])
    if extra_env:
        env.update(extra_env)

    # Patch WORKSPACE in the script by prepending a variable override
    script = f'WORKSPACE="{mock_env["workspace"]}" source {ENTRYPOINT}'

    result = subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    if not expect_fail:
        assert result.returncode == 0, (
            f"Entrypoint failed (rc={result.returncode}):\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

    return result


def _read_log(mock_env: dict[str, Any]) -> list[str]:
    """Read the commands log file."""
    log_file: Path = mock_env["log_file"]
    if log_file.exists():
        return list(log_file.read_text().strip().splitlines())
    return []


# ---------------------------------------------------------------------------
# No repos
# ---------------------------------------------------------------------------


class TestNoRepos:
    """Tests when no repos are specified."""

    def test_runs_claude_without_repos(self, mock_env: dict[str, Any]) -> None:
        """When FLAUDE_REPOS is not set, Claude runs directly."""
        result = _run_entrypoint(mock_env)
        assert "[flaude] No repositories to clone" in result.stdout
        assert "[flaude] Running Claude Code" in result.stdout
        log = _read_log(mock_env)
        assert any("claude" in line for line in log)

    def test_runs_claude_with_empty_repos(self, mock_env: dict[str, Any]) -> None:
        """When FLAUDE_REPOS is '[]', Claude runs directly."""
        result = _run_entrypoint(mock_env, {"FLAUDE_REPOS": "[]"})
        assert "[flaude] No repositories to clone" in result.stdout

    def test_runs_claude_with_empty_string(self, mock_env: dict[str, Any]) -> None:
        """When FLAUDE_REPOS is empty string, Claude runs directly."""
        result = _run_entrypoint(mock_env, {"FLAUDE_REPOS": ""})
        assert "[flaude] No repositories to clone" in result.stdout


# ---------------------------------------------------------------------------
# Single repo
# ---------------------------------------------------------------------------


class TestSingleRepo:
    """Tests for cloning a single repository."""

    def test_clones_single_repo(self, mock_env: dict[str, Any]) -> None:
        """A single repo URL is cloned into /workspace/<repo-name>."""
        repos = json.dumps([{"url": "https://github.com/org/my-repo"}])
        result = _run_entrypoint(mock_env, {"FLAUDE_REPOS": repos})

        log = _read_log(mock_env)
        clone_lines = [line for line in log if "git clone" in line]
        assert len(clone_lines) == 1
        assert "my-repo" in clone_lines[0]
        assert "[flaude] All 1 repositories cloned" in result.stdout

    def test_single_repo_sets_workdir(self, mock_env: dict[str, Any]) -> None:
        """With one repo, working directory is set to that repo's dir."""
        repos = json.dumps([{"url": "https://github.com/org/my-repo"}])
        result = _run_entrypoint(mock_env, {"FLAUDE_REPOS": repos})

        assert "[flaude] Working directory set to" in result.stdout
        assert "my-repo" in result.stdout

    def test_single_repo_with_custom_target(self, mock_env: dict[str, Any]) -> None:
        """A repo with target_dir clones into the specified directory."""
        repos = json.dumps(
            [{"url": "https://github.com/org/my-repo", "target_dir": "custom"}]
        )
        _run_entrypoint(mock_env, {"FLAUDE_REPOS": repos})

        log = _read_log(mock_env)
        clone_lines = [line for line in log if "git clone" in line]
        assert "custom" in clone_lines[0]

    def test_single_repo_with_branch(self, mock_env: dict[str, Any]) -> None:
        """A repo with branch uses --branch flag."""
        repos = json.dumps(
            [{"url": "https://github.com/org/my-repo", "branch": "develop"}]
        )
        _run_entrypoint(mock_env, {"FLAUDE_REPOS": repos})

        log = _read_log(mock_env)
        clone_lines = [line for line in log if "git clone" in line]
        assert "--branch develop" in clone_lines[0]


# ---------------------------------------------------------------------------
# Multiple repos
# ---------------------------------------------------------------------------


class TestMultipleRepos:
    """Tests for cloning multiple repositories."""

    def test_clones_multiple_repos(self, mock_env: dict[str, Any]) -> None:
        """Multiple repos are all cloned."""
        repos = json.dumps(
            [
                {"url": "https://github.com/org/repo-a"},
                {"url": "https://github.com/org/repo-b"},
            ]
        )
        result = _run_entrypoint(mock_env, {"FLAUDE_REPOS": repos})

        log = _read_log(mock_env)
        clone_lines = [line for line in log if "git clone" in line]
        assert len(clone_lines) == 2
        assert "[flaude] All 2 repositories cloned" in result.stdout

    def test_multiple_repos_stay_in_workspace(self, mock_env: dict[str, Any]) -> None:
        """With multiple repos, working directory stays at /workspace."""
        repos = json.dumps(
            [
                {"url": "https://github.com/org/repo-a"},
                {"url": "https://github.com/org/repo-b"},
            ]
        )
        result = _run_entrypoint(mock_env, {"FLAUDE_REPOS": repos})

        # Should NOT contain "Working directory set to" with a specific repo
        assert "Working directory set to" not in result.stdout


# ---------------------------------------------------------------------------
# Git credentials
# ---------------------------------------------------------------------------


class TestGitCredentials:
    """Tests for git credential configuration."""

    def test_configures_git_credentials(self, mock_env: dict[str, Any]) -> None:
        """Git credentials are configured when GITHUB_USERNAME and TOKEN are set."""
        repos = json.dumps([{"url": "https://github.com/org/private-repo"}])
        result = _run_entrypoint(
            mock_env,
            {
                "FLAUDE_REPOS": repos,
                "GITHUB_USERNAME": "testuser",
                "GITHUB_TOKEN": "ghp_test123",
            },
        )

        assert "Git credentials configured for testuser" in result.stdout
        log = _read_log(mock_env)
        config_lines = [line for line in log if "git config" in line]
        assert len(config_lines) >= 1

    def test_no_credentials_without_github_vars(self, mock_env: dict[str, Any]) -> None:
        """Git credentials are not configured without GITHUB vars."""
        repos = json.dumps([{"url": "https://github.com/org/public-repo"}])
        result = _run_entrypoint(mock_env, {"FLAUDE_REPOS": repos})

        assert "Git credentials configured" not in result.stdout


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Tests for error cases."""

    def test_fails_without_prompt(self, mock_env: dict[str, Any]) -> None:
        """Entrypoint fails when FLAUDE_PROMPT is not set."""
        env = dict(mock_env["env"])
        del env["FLAUDE_PROMPT"]

        script = f'WORKSPACE="{mock_env["workspace"]}" source {ENTRYPOINT}'
        result = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0
        assert "FLAUDE_PROMPT is not set" in result.stderr

    def test_fails_on_invalid_json(self, mock_env: dict[str, Any]) -> None:
        """Entrypoint fails when FLAUDE_REPOS is invalid JSON."""
        result = _run_entrypoint(
            mock_env,
            {"FLAUDE_REPOS": "not valid json"},
            expect_fail=True,
        )
        assert result.returncode != 0
        assert "not valid JSON" in result.stderr

    def test_skips_empty_url(self, mock_env: dict[str, Any]) -> None:
        """Repos with empty URLs are skipped with a warning."""
        repos = json.dumps(
            [
                {"url": ""},
                {"url": "https://github.com/org/valid-repo"},
            ]
        )
        result = _run_entrypoint(mock_env, {"FLAUDE_REPOS": repos})

        assert "has no URL, skipping" in result.stderr
        log = _read_log(mock_env)
        clone_lines = [line for line in log if "git clone" in line]
        assert len(clone_lines) == 1

    def test_fails_on_clone_failure(self, mock_env: dict[str, Any]) -> None:
        """Entrypoint fails when git clone fails."""
        # Replace mock git with one that fails on clone
        mock_git = mock_env["bin_dir"] / "git"
        mock_git.write_text(
            textwrap.dedent("""\
            #!/usr/bin/env bash
            if [ "$1" = "clone" ]; then
                echo "fatal: repository not found" >&2
                exit 128
            fi
            exit 0
        """)
        )

        repos = json.dumps([{"url": "https://github.com/org/nonexistent"}])
        result = _run_entrypoint(
            mock_env,
            {"FLAUDE_REPOS": repos},
            expect_fail=True,
        )
        assert result.returncode != 0
        assert "Failed to clone" in result.stderr


# ---------------------------------------------------------------------------
# Repo name derivation
# ---------------------------------------------------------------------------


class TestRepoNameDerivation:
    """Tests for deriving target directory names from URLs."""

    def test_strips_dot_git_suffix(self, mock_env: dict[str, Any]) -> None:
        """URLs ending in .git have the suffix stripped."""
        repos = json.dumps([{"url": "https://github.com/org/my-repo.git"}])
        _run_entrypoint(mock_env, {"FLAUDE_REPOS": repos})

        log = _read_log(mock_env)
        clone_lines = [line for line in log if "git clone" in line]
        # Should clone into my-repo, not my-repo.git
        assert "my-repo" in clone_lines[0]
        assert (
            ".git" not in clone_lines[0].split()[-1]
        )  # target dir shouldn't end in .git

    def test_uses_target_dir_override(self, mock_env: dict[str, Any]) -> None:
        """target_dir overrides the derived name."""
        repos = json.dumps(
            [{"url": "https://github.com/org/my-repo", "target_dir": "custom-name"}]
        )
        _run_entrypoint(mock_env, {"FLAUDE_REPOS": repos})

        log = _read_log(mock_env)
        clone_lines = [line for line in log if "git clone" in line]
        assert "custom-name" in clone_lines[0]


# ---------------------------------------------------------------------------
# Session mode
# ---------------------------------------------------------------------------


class TestSessionMode:
    """Tests for FLAUDE_SESSION_ID session mode."""

    def test_session_mode_uses_volume_workspace(self, mock_env: dict[str, Any]) -> None:
        """When FLAUDE_SESSION_ID is set, workspace defaults to /data/workspace."""
        data_workspace = mock_env["tmp_path"] / "data" / "workspace"
        data_workspace.mkdir(parents=True)
        data_claude = mock_env["tmp_path"] / "data" / "claude"
        data_claude.mkdir(parents=True)

        result = _run_entrypoint(
            mock_env,
            {
                "FLAUDE_SESSION_ID": "ses-abc123",
                "WORKSPACE": str(data_workspace),
                "CLAUDE_CONFIG_DIR": str(data_claude),
            },
        )
        assert "[flaude] Session mode: session_id=ses-abc123" in result.stdout
        assert "[flaude:session:ses-abc123]" in result.stdout

    def test_session_mode_emits_session_marker(self, mock_env: dict[str, Any]) -> None:
        """Session mode emits the [flaude:session:<id>] marker line."""
        data_workspace = mock_env["tmp_path"] / "data" / "workspace"
        data_workspace.mkdir(parents=True)
        data_claude = mock_env["tmp_path"] / "data" / "claude"
        data_claude.mkdir(parents=True)

        result = _run_entrypoint(
            mock_env,
            {
                "FLAUDE_SESSION_ID": "ses-xyz",
                "WORKSPACE": str(data_workspace),
                "CLAUDE_CONFIG_DIR": str(data_claude),
            },
        )
        assert "[flaude:session:ses-xyz]" in result.stdout

    def test_session_mode_skips_clone_when_workspace_populated(
        self, mock_env: dict[str, Any]
    ) -> None:
        """When workspace already has content, clone is skipped (session resume)."""
        # Use the mock workspace (the one _run_entrypoint injects) and populate it.
        workspace: Path = mock_env["workspace"]
        (workspace / "existing_file.txt").write_text("already here")
        data_claude = mock_env["tmp_path"] / "data" / "claude"
        data_claude.mkdir(parents=True)

        repos = json.dumps([{"url": "https://github.com/org/my-repo"}])
        result = _run_entrypoint(
            mock_env,
            {
                "FLAUDE_SESSION_ID": "ses-resume",
                "CLAUDE_CONFIG_DIR": str(data_claude),
                "FLAUDE_REPOS": repos,
            },
        )

        assert "skipping clone (session resume)" in result.stdout
        log = _read_log(mock_env)
        clone_lines = [line for line in log if "git clone" in line]
        assert len(clone_lines) == 0

    def test_session_mode_clones_when_workspace_empty(
        self, mock_env: dict[str, Any]
    ) -> None:
        """When workspace is empty in session mode, repos are still cloned."""
        data_workspace = mock_env["tmp_path"] / "data" / "workspace"
        data_workspace.mkdir(parents=True)
        data_claude = mock_env["tmp_path"] / "data" / "claude"
        data_claude.mkdir(parents=True)

        repos = json.dumps([{"url": "https://github.com/org/my-repo"}])
        result = _run_entrypoint(
            mock_env,
            {
                "FLAUDE_SESSION_ID": "ses-new",
                "WORKSPACE": str(data_workspace),
                "CLAUDE_CONFIG_DIR": str(data_claude),
                "FLAUDE_REPOS": repos,
            },
        )

        assert "skipping clone" not in result.stdout
        log = _read_log(mock_env)
        clone_lines = [line for line in log if "git clone" in line]
        assert len(clone_lines) == 1

    def test_new_session_uses_session_id_flag(self, mock_env: dict[str, Any]) -> None:
        """First turn of a session uses --session-id (no transcript exists)."""
        data_workspace = mock_env["tmp_path"] / "data" / "workspace"
        data_workspace.mkdir(parents=True)
        data_claude = mock_env["tmp_path"] / "data" / "claude"
        data_claude.mkdir(parents=True)

        result = _run_entrypoint(
            mock_env,
            {
                "FLAUDE_SESSION_ID": "ses-new123",
                "WORKSPACE": str(data_workspace),
                "CLAUDE_CONFIG_DIR": str(data_claude),
            },
        )

        assert "[flaude] Starting new session ses-new123" in result.stdout
        log = _read_log(mock_env)
        claude_lines = [line for line in log if line.startswith("claude")]
        assert len(claude_lines) == 1
        assert "--session-id ses-new123" in claude_lines[0]

    def test_resume_session_uses_resume_flag(self, mock_env: dict[str, Any]) -> None:
        """Subsequent turns use --resume when a transcript file exists."""
        data_claude = mock_env["tmp_path"] / "data" / "claude"
        data_claude.mkdir(parents=True)

        # The script does `cd "$WORKSPACE"` then encodes $PWD.
        # _run_entrypoint sets WORKSPACE to mock_env["workspace"], so $PWD
        # after cd will be that path (resolved via the shell).
        # Mirror the script's encoding: replace non-alphanumeric chars with '-'.
        workspace: Path = mock_env["workspace"]
        cmd = (
            f"cd '{workspace}' && "
            "echo \"$PWD\" | sed 's|[^a-zA-Z0-9]|-|g'"
        )
        encoded = subprocess.check_output(
            ["bash", "-c", cmd], text=True,
        ).strip()
        projects_dir = data_claude / "projects" / encoded
        projects_dir.mkdir(parents=True)
        session_file = projects_dir / "ses-resume99.jsonl"
        session_file.write_text('{"role":"assistant","content":"hi"}\n')

        result = _run_entrypoint(
            mock_env,
            {
                "FLAUDE_SESSION_ID": "ses-resume99",
                "CLAUDE_CONFIG_DIR": str(data_claude),
            },
        )

        assert "[flaude] Resuming session ses-resume99" in result.stdout
        log = _read_log(mock_env)
        claude_lines = [line for line in log if line.startswith("claude")]
        assert len(claude_lines) == 1
        assert "--resume ses-resume99" in claude_lines[0]

    def test_one_shot_mode_no_session_args(self, mock_env: dict[str, Any]) -> None:
        """Without FLAUDE_SESSION_ID, claude is invoked without session flags."""
        result = _run_entrypoint(mock_env)

        assert "[flaude:session:" not in result.stdout
        log = _read_log(mock_env)
        claude_lines = [line for line in log if line.startswith("claude")]
        assert len(claude_lines) == 1
        assert "--resume" not in claude_lines[0]
        assert "--session-id" not in claude_lines[0]
