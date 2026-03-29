"""Tests for Sub-AC 5.1: Fly.io machine startup environment.

Verifies that when a machine boots:
  - Claude Code is ready to invoke (entrypoint calls ``claude -p``)
  - CLAUDE_CODE_OAUTH_TOKEN env var is present and forwarded to the process
  - The entrypoint fails fast with a clear error if the token is absent
  - Non-interactive (print) mode is used for all executions

These tests exercise the shell entrypoint via a mock ``claude`` binary,
so they do not require a real Docker image or Fly.io account.
"""

from __future__ import annotations

import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

from flaude.machine_config import MachineConfig, build_machine_config

# Path to the entrypoint script under test
ENTRYPOINT = Path(__file__).parent.parent / "flaude" / "entrypoint.sh"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def startup_env(tmp_path: Path) -> dict:
    """Minimal mock environment for testing Claude Code startup.

    Provides:
    - A workspace directory
    - A mock ``claude`` that captures invocation details
    - A mock ``git`` (no-op)
    - CLAUDE_CODE_OAUTH_TOKEN set in the environment
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # File that the mock claude writes its argv to
    argv_file = tmp_path / "claude_argv.txt"
    # File that the mock claude writes its CLAUDE_CODE_OAUTH_TOKEN env value to
    token_file = tmp_path / "claude_token.txt"

    # Mock claude: captures argv and env token
    mock_claude = bin_dir / "claude"
    mock_claude.write_text(
        textwrap.dedent(f"""\
        #!/usr/bin/env bash
        # Write all arguments
        printf '%s\\n' "$@" > {argv_file}
        # Write the OAuth token from environment
        printf '%s' "${{CLAUDE_CODE_OAUTH_TOKEN:-NOT_SET}}" > {token_file}
        echo "Claude Code output"
        exit 0
    """)
    )
    mock_claude.chmod(mock_claude.stat().st_mode | stat.S_IEXEC)

    # Mock git: no-op
    mock_git = bin_dir / "git"
    mock_git.write_text(
        textwrap.dedent("""\
        #!/usr/bin/env bash
        if [ "$1" = "clone" ]; then
            target="${@: -1}"
            mkdir -p "$target"
        fi
        exit 0
    """)
    )
    mock_git.chmod(mock_git.stat().st_mode | stat.S_IEXEC)

    home = tmp_path / "home"
    home.mkdir()

    return {
        "workspace": workspace,
        "bin_dir": bin_dir,
        "argv_file": argv_file,
        "token_file": token_file,
        "tmp_path": tmp_path,
        "base_env": {
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "HOME": str(home),
            "FLAUDE_PROMPT": "Fix the bug",
            "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oauth-test-token",
        },
    }


def _run_entrypoint(
    startup_env: dict,
    extra_env: dict | None = None,
    expect_fail: bool = False,
) -> subprocess.CompletedProcess:
    env = dict(startup_env["base_env"])
    if extra_env:
        env.update(extra_env)

    script = f'WORKSPACE="{startup_env["workspace"]}" source {ENTRYPOINT}'
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
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    return result


# ---------------------------------------------------------------------------
# Authentication: CLAUDE_CODE_OAUTH_TOKEN presence and validation
# ---------------------------------------------------------------------------


class TestOAuthTokenValidation:
    """The entrypoint must fail fast when CLAUDE_CODE_OAUTH_TOKEN is absent."""

    def test_fails_without_oauth_token(self, startup_env: dict) -> None:
        """Entrypoint exits non-zero when CLAUDE_CODE_OAUTH_TOKEN is missing."""
        env = dict(startup_env["base_env"])
        del env["CLAUDE_CODE_OAUTH_TOKEN"]

        script = f'WORKSPACE="{startup_env["workspace"]}" source {ENTRYPOINT}'
        result = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0

    def test_error_message_mentions_token(self, startup_env: dict) -> None:
        """Error message names the missing variable."""
        env = dict(startup_env["base_env"])
        del env["CLAUDE_CODE_OAUTH_TOKEN"]

        script = f'WORKSPACE="{startup_env["workspace"]}" source {ENTRYPOINT}'
        result = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert "CLAUDE_CODE_OAUTH_TOKEN" in result.stderr

    def test_fails_with_empty_oauth_token(self, startup_env: dict) -> None:
        """Entrypoint exits non-zero when CLAUDE_CODE_OAUTH_TOKEN is empty."""
        result = _run_entrypoint(
            startup_env,
            extra_env={"CLAUDE_CODE_OAUTH_TOKEN": ""},
            expect_fail=True,
        )
        assert result.returncode != 0
        assert "CLAUDE_CODE_OAUTH_TOKEN" in result.stderr

    def test_succeeds_with_valid_token(self, startup_env: dict) -> None:
        """Entrypoint runs normally when token is provided."""
        result = _run_entrypoint(startup_env)
        assert result.returncode == 0

    def test_token_forwarded_to_claude_process(self, startup_env: dict) -> None:
        """CLAUDE_CODE_OAUTH_TOKEN is present in claude's process environment."""
        _run_entrypoint(startup_env)

        token_seen = startup_env["token_file"].read_text()
        assert token_seen == "sk-ant-oauth-test-token"

    def test_token_value_preserved_exactly(self, startup_env: dict) -> None:
        """The exact token value is forwarded without modification."""
        custom_token = "sk-ant-oauth-abcdef123456"
        _run_entrypoint(
            startup_env, extra_env={"CLAUDE_CODE_OAUTH_TOKEN": custom_token}
        )

        token_seen = startup_env["token_file"].read_text()
        assert token_seen == custom_token


# ---------------------------------------------------------------------------
# Non-interactive execution mode
# ---------------------------------------------------------------------------


class TestNonInteractiveMode:
    """Claude Code must be invoked in non-interactive (print) mode."""

    def test_claude_invoked_with_print_flag(self, startup_env: dict) -> None:
        """The entrypoint passes -p (--print) flag to claude."""
        _run_entrypoint(startup_env)

        argv_lines = startup_env["argv_file"].read_text().splitlines()
        assert "-p" in argv_lines

    def test_claude_uses_separator_before_prompt(self, startup_env: dict) -> None:
        """The entrypoint uses -- to separate flags from the prompt."""
        _run_entrypoint(startup_env)

        argv_lines = startup_env["argv_file"].read_text().splitlines()
        assert "--" in argv_lines

    def test_prompt_is_last_argument(self, startup_env: dict) -> None:
        """The prompt string is the final argument to claude."""
        prompt = "Fix the bug"
        _run_entrypoint(startup_env, extra_env={"FLAUDE_PROMPT": prompt})

        argv_lines = startup_env["argv_file"].read_text().splitlines()
        assert argv_lines[-1] == prompt

    def test_flag_order_print_then_separator_then_prompt(
        self, startup_env: dict
    ) -> None:
        """Invocation follows the pattern: claude -p -- <prompt>."""
        _run_entrypoint(startup_env, extra_env={"FLAUDE_PROMPT": "Test prompt"})

        argv_lines = startup_env["argv_file"].read_text().splitlines()
        # Must contain -p, --, and prompt in that order
        idx_p = argv_lines.index("-p")
        idx_sep = argv_lines.index("--")
        idx_prompt = argv_lines.index("Test prompt")
        assert idx_p < idx_sep < idx_prompt

    def test_flaude_log_shows_running_claude(self, startup_env: dict) -> None:
        """The entrypoint logs before launching Claude."""
        result = _run_entrypoint(startup_env)
        assert "[flaude] Running Claude Code" in result.stdout


# ---------------------------------------------------------------------------
# Machine config: token injected into Fly machine env vars
# ---------------------------------------------------------------------------


class TestMachineConfigTokenInjection:
    """MachineConfig.build_machine_config injects CLAUDE_CODE_OAUTH_TOKEN."""

    def test_token_in_payload_env(self) -> None:
        """The OAuth token appears in the Fly machine env var payload."""
        config = MachineConfig(
            claude_code_oauth_token="sk-ant-oauth-my-token",
            prompt="Write tests",
        )
        payload = build_machine_config(config)
        assert (
            payload["config"]["env"]["CLAUDE_CODE_OAUTH_TOKEN"]
            == "sk-ant-oauth-my-token"
        )

    def test_missing_token_raises_valueerror(self) -> None:
        """build_machine_config raises ValueError when token is empty."""
        config = MachineConfig(prompt="Write tests")
        with pytest.raises(ValueError, match="claude_code_oauth_token"):
            build_machine_config(config)

    def test_token_not_exposed_in_metadata(self) -> None:
        """The OAuth token is not accidentally written into machine metadata."""
        config = MachineConfig(
            claude_code_oauth_token="secret-token",
            prompt="Write tests",
        )
        payload = build_machine_config(config)
        metadata_str = str(payload["config"].get("metadata", {}))
        assert "secret-token" not in metadata_str

    def test_token_separate_from_github_creds(self) -> None:
        """OAuth token and GitHub token are independent env vars."""
        config = MachineConfig(
            claude_code_oauth_token="claude-token",
            github_token="github-token",
            github_username="myuser",
            prompt="Write tests",
        )
        payload = build_machine_config(config)
        env = payload["config"]["env"]
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "claude-token"
        assert env["GITHUB_TOKEN"] == "github-token"


# ---------------------------------------------------------------------------
# Output format configuration
# ---------------------------------------------------------------------------


class TestOutputFormat:
    """Claude Code output format is configurable via FLAUDE_OUTPUT_FORMAT."""

    def test_output_format_passed_when_set(self, startup_env: dict) -> None:
        """--output-format and value appear in argv when env var is set."""
        _run_entrypoint(startup_env, extra_env={"FLAUDE_OUTPUT_FORMAT": "stream-json"})
        argv_lines = startup_env["argv_file"].read_text().splitlines()
        assert "--output-format" in argv_lines
        assert "stream-json" in argv_lines

    def test_verbose_added_for_stream_json(self, startup_env: dict) -> None:
        """--verbose is added automatically when stream-json is requested."""
        _run_entrypoint(startup_env, extra_env={"FLAUDE_OUTPUT_FORMAT": "stream-json"})
        argv_lines = startup_env["argv_file"].read_text().splitlines()
        assert "--verbose" in argv_lines

    def test_verbose_not_added_for_json_format(self, startup_env: dict) -> None:
        """--verbose is NOT added for non-stream-json formats."""
        _run_entrypoint(startup_env, extra_env={"FLAUDE_OUTPUT_FORMAT": "json"})
        argv_lines = startup_env["argv_file"].read_text().splitlines()
        assert "--output-format" in argv_lines
        assert "json" in argv_lines
        assert "--verbose" not in argv_lines

    def test_no_output_format_when_unset(self, startup_env: dict) -> None:
        """No --output-format flag when env var is not set."""
        _run_entrypoint(startup_env)
        argv_lines = startup_env["argv_file"].read_text().splitlines()
        assert "--output-format" not in argv_lines

    def test_output_format_before_separator(self, startup_env: dict) -> None:
        """Output format flags appear before the -- separator."""
        _run_entrypoint(startup_env, extra_env={"FLAUDE_OUTPUT_FORMAT": "stream-json"})
        argv_lines = startup_env["argv_file"].read_text().splitlines()
        idx_fmt = argv_lines.index("--output-format")
        idx_sep = argv_lines.index("--")
        assert idx_fmt < idx_sep


class TestMachineConfigOutputFormat:
    """MachineConfig.output_format is injected as FLAUDE_OUTPUT_FORMAT."""

    def test_output_format_in_payload_env(self) -> None:
        config = MachineConfig(
            claude_code_oauth_token="token",
            prompt="test",
            output_format="stream-json",
        )
        payload = build_machine_config(config)
        assert payload["config"]["env"]["FLAUDE_OUTPUT_FORMAT"] == "stream-json"

    def test_no_output_format_env_when_empty(self) -> None:
        config = MachineConfig(
            claude_code_oauth_token="token",
            prompt="test",
        )
        payload = build_machine_config(config)
        assert "FLAUDE_OUTPUT_FORMAT" not in payload["config"]["env"]


# ---------------------------------------------------------------------------
# Entrypoint startup sequence
# ---------------------------------------------------------------------------


class TestStartupSequence:
    """The entrypoint should validate environment before doing any work."""

    def test_token_validated_before_clone(self, startup_env: dict) -> None:
        """Token validation runs before repo cloning starts."""
        import json

        env = dict(startup_env["base_env"])
        del env["CLAUDE_CODE_OAUTH_TOKEN"]
        # Add repos to make clone step visible if it runs
        env["FLAUDE_REPOS"] = json.dumps([{"url": "https://github.com/org/repo"}])

        script = f'WORKSPACE="{startup_env["workspace"]}" source {ENTRYPOINT}'
        result = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0
        # Clone should not have run
        assert "Cloning repositories" not in result.stdout

    def test_token_validated_before_prompt_check(self, startup_env: dict) -> None:
        """Token check runs even when prompt is also missing."""
        env = dict(startup_env["base_env"])
        del env["CLAUDE_CODE_OAUTH_TOKEN"]
        del env["FLAUDE_PROMPT"]

        script = f'WORKSPACE="{startup_env["workspace"]}" source {ENTRYPOINT}'
        result = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0
        # CLAUDE_CODE_OAUTH_TOKEN error takes priority
        assert "CLAUDE_CODE_OAUTH_TOKEN" in result.stderr

    def test_execution_starts_message_shown(self, startup_env: dict) -> None:
        """The entrypoint logs its start before any validation."""
        result = _run_entrypoint(startup_env)
        assert "[flaude] Starting execution" in result.stdout
