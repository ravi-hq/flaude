"""Tests for AC 5: Claude Code runs the user-provided prompt string inside the machine.

Validates the full prompt pipeline:
  MachineConfig.prompt → FLAUDE_PROMPT env var → entrypoint.sh
  → claude -p -- "$FLAUDE_PROMPT"

Tests cover:
- Prompt text arrives correctly at the claude CLI invocation
- Multi-line prompts are preserved
- Special characters in prompts are handled safely
- Prompts starting with dashes don't get parsed as CLI flags
- The entrypoint runs claude in the correct working directory
- Non-zero exit from claude propagates correctly
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

from flaude.machine_config import MachineConfig, build_machine_config

# Path to the entrypoint script
ENTRYPOINT = Path(__file__).parent.parent / "flaude" / "entrypoint.sh"


@pytest.fixture
def prompt_env(tmp_path: Path) -> dict:
    """Create a mock environment that captures the exact prompt passed to claude."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    prompt_file = tmp_path / "captured_prompt.txt"
    exit_code_file = tmp_path / "claude_exit_code.txt"
    exit_code_file.write_text("0")

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

    # Mock claude: captures the prompt to a file for assertion.
    # Handles `claude -p -- "prompt"` — the prompt is the arg after `--`.
    mock_claude = bin_dir / "claude"
    mock_claude.write_text(
        textwrap.dedent(f"""\
        #!/usr/bin/env bash
        # Skip flags and -- separator to find the prompt argument
        PROMPT=""
        SKIP_NEXT=false
        PAST_SEPARATOR=false
        for arg in "$@"; do
            if [ "$SKIP_NEXT" = true ]; then
                SKIP_NEXT=false
                continue
            fi
            if [ "$arg" = "--" ]; then
                PAST_SEPARATOR=true
                continue
            fi
            if [ "$arg" = "-p" ] || [ "$arg" = "--print" ]; then
                continue
            fi
            # If we're past -- or this is the first non-flag arg, it's the prompt
            PROMPT="$arg"
            break
        done
        printf '%s' "$PROMPT" > {prompt_file}
        echo "Claude says: processing prompt"
        EXIT_CODE=$(cat {exit_code_file})
        exit $EXIT_CODE
    """)
    )
    mock_claude.chmod(mock_claude.stat().st_mode | stat.S_IEXEC)

    home = tmp_path / "home"
    home.mkdir()

    env = {
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "HOME": str(home),
        "CLAUDE_CODE_OAUTH_TOKEN": "test-oauth-token",
    }

    return {
        "workspace": workspace,
        "bin_dir": bin_dir,
        "prompt_file": prompt_file,
        "exit_code_file": exit_code_file,
        "env": env,
        "tmp_path": tmp_path,
    }


def _run(
    prompt_env: dict,
    prompt: str,
    extra_env: dict | None = None,
    expect_fail: bool = False,
) -> subprocess.CompletedProcess:
    """Run the entrypoint with the given prompt."""
    env = dict(prompt_env["env"])
    env["FLAUDE_PROMPT"] = prompt
    if extra_env:
        env.update(extra_env)

    script = f'WORKSPACE="{prompt_env["workspace"]}" source {ENTRYPOINT}'
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


def _captured_prompt(prompt_env: dict) -> str:
    """Read the prompt that was captured by the mock claude."""
    return str(prompt_env["prompt_file"].read_text())


# ---------------------------------------------------------------------------
# Basic prompt delivery
# ---------------------------------------------------------------------------


class TestPromptDelivery:
    """Verify that the prompt string reaches the claude CLI correctly."""

    def test_simple_prompt(self, prompt_env: dict) -> None:
        """A simple text prompt is passed to claude."""
        _run(prompt_env, "Fix the bug in main.py")
        assert _captured_prompt(prompt_env) == "Fix the bug in main.py"

    def test_prompt_with_spaces(self, prompt_env: dict) -> None:
        """Prompts with spaces are preserved as a single argument."""
        _run(prompt_env, "Please review the code and fix any issues")
        assert (
            _captured_prompt(prompt_env) == "Please review the code and fix any issues"
        )

    def test_prompt_appears_in_flaude_log(self, prompt_env: dict) -> None:
        """The flaude log shows Claude Code is running."""
        result = _run(prompt_env, "Test prompt")
        assert "[flaude] Running Claude Code" in result.stdout

    def test_prompt_exit_code_logged(self, prompt_env: dict) -> None:
        """Exit code is logged after Claude Code completes."""
        result = _run(prompt_env, "Test prompt")
        assert "[flaude] Claude Code exited with code 0" in result.stdout
        assert "[flaude:exit:0]" in result.stdout


# ---------------------------------------------------------------------------
# Special characters and edge cases
# ---------------------------------------------------------------------------


class TestPromptEdgeCases:
    """Prompts with special characters, newlines, etc."""

    def test_prompt_with_single_quotes(self, prompt_env: dict) -> None:
        """Single quotes in prompts are handled."""
        _run(prompt_env, "Fix the bug in O'Brien's module")
        assert _captured_prompt(prompt_env) == "Fix the bug in O'Brien's module"

    def test_prompt_with_double_quotes(self, prompt_env: dict) -> None:
        """Double quotes in prompts are handled."""
        _run(prompt_env, 'Add a "hello world" function')
        assert _captured_prompt(prompt_env) == 'Add a "hello world" function'

    def test_prompt_with_backticks(self, prompt_env: dict) -> None:
        """Backticks in prompts are preserved."""
        _run(prompt_env, "Run `pytest` and fix failures")
        assert _captured_prompt(prompt_env) == "Run `pytest` and fix failures"

    def test_prompt_with_dollar_signs(self, prompt_env: dict) -> None:
        """Dollar signs don't cause variable expansion issues."""
        # Dollar sign at end of string (no variable name after it)
        _run(prompt_env, "Format the price as $100")
        assert _captured_prompt(prompt_env) == "Format the price as $100"

    def test_prompt_starting_with_dash(self, prompt_env: dict) -> None:
        """Prompts starting with - are not parsed as CLI flags."""
        _run(prompt_env, "-v flag should be added to the test runner")
        assert (
            _captured_prompt(prompt_env) == "-v flag should be added to the test runner"
        )

    def test_prompt_starting_with_double_dash(self, prompt_env: dict) -> None:
        """Prompts starting with -- are not parsed as CLI flags."""
        _run(prompt_env, "--verbose mode needs fixing")
        assert _captured_prompt(prompt_env) == "--verbose mode needs fixing"

    def test_prompt_with_newlines(self, prompt_env: dict) -> None:
        """Multi-line prompts are preserved."""
        multiline = "Step 1: Read the code\nStep 2: Fix bugs\nStep 3: Add tests"
        _run(prompt_env, multiline)
        assert _captured_prompt(prompt_env) == multiline

    def test_prompt_with_parentheses_and_brackets(self, prompt_env: dict) -> None:
        """Parentheses and brackets are safe."""
        _run(prompt_env, "Fix function(arg) and array[0]")
        assert _captured_prompt(prompt_env) == "Fix function(arg) and array[0]"


# ---------------------------------------------------------------------------
# Non-zero exit propagation
# ---------------------------------------------------------------------------


class TestExitCodePropagation:
    """Verify that non-zero exit codes from claude propagate correctly."""

    def test_nonzero_exit_propagates(self, prompt_env: dict) -> None:
        """Non-zero exit from claude causes entrypoint to exit with same code."""
        prompt_env["exit_code_file"].write_text("1")
        result = _run(prompt_env, "This will fail", expect_fail=True)
        assert result.returncode == 1
        assert "[flaude:exit:1]" in result.stdout

    def test_exit_code_137_propagates(self, prompt_env: dict) -> None:
        """OOM-killed exit code 137 propagates."""
        prompt_env["exit_code_file"].write_text("137")
        result = _run(prompt_env, "OOM test", expect_fail=True)
        assert result.returncode == 137
        assert "[flaude:exit:137]" in result.stdout


# ---------------------------------------------------------------------------
# Config → env var pipeline
# ---------------------------------------------------------------------------


class TestConfigToEnvPipeline:
    """Verify prompt flows from MachineConfig to the FLAUDE_PROMPT env var."""

    def test_prompt_in_machine_config_payload(self) -> None:
        """MachineConfig.prompt becomes FLAUDE_PROMPT in the Fly API payload."""
        config = MachineConfig(
            claude_code_oauth_token="test-token",
            prompt="Refactor the database layer",
        )
        payload = build_machine_config(config)
        env = payload["config"]["env"]
        assert env["FLAUDE_PROMPT"] == "Refactor the database layer"

    def test_multiline_prompt_in_payload(self) -> None:
        """Multi-line prompts are preserved in the env var payload."""
        prompt = "Step 1: Read\nStep 2: Fix\nStep 3: Test"
        config = MachineConfig(
            claude_code_oauth_token="test-token",
            prompt=prompt,
        )
        payload = build_machine_config(config)
        assert payload["config"]["env"]["FLAUDE_PROMPT"] == prompt

    def test_prompt_with_special_chars_in_payload(self) -> None:
        """Special characters in prompts survive config building."""
        prompt = "Fix O'Brien's \"helper\" function ($cost > 0)"
        config = MachineConfig(
            claude_code_oauth_token="test-token",
            prompt=prompt,
        )
        payload = build_machine_config(config)
        assert payload["config"]["env"]["FLAUDE_PROMPT"] == prompt

    def test_empty_prompt_rejected(self) -> None:
        """Empty prompt raises ValueError during config building."""
        config = MachineConfig(
            claude_code_oauth_token="test-token",
            prompt="",
        )
        with pytest.raises(ValueError, match="prompt"):
            build_machine_config(config)


# ---------------------------------------------------------------------------
# Working directory for prompt execution
# ---------------------------------------------------------------------------


class TestPromptWorkingDirectory:
    """Verify claude runs in the correct working directory."""

    def test_runs_in_workspace_without_repos(self, prompt_env: dict) -> None:
        """Without repos, claude runs in the workspace root."""
        # Enhance mock claude to capture pwd
        cwd_file = prompt_env["tmp_path"] / "captured_cwd.txt"
        mock_claude = prompt_env["bin_dir"] / "claude"
        mock_claude.write_text(
            textwrap.dedent(f"""\
            #!/usr/bin/env bash
            pwd > {cwd_file}
            printf '%s' "${{@: -1}}" > {prompt_env["prompt_file"]}
            echo "done"
            exit 0
        """)
        )

        _run(prompt_env, "Test prompt")
        cwd = cwd_file.read_text().strip()
        assert cwd == str(prompt_env["workspace"])

    def test_runs_in_repo_dir_with_single_repo(self, prompt_env: dict) -> None:
        """With a single repo, claude runs inside the cloned repo directory."""
        cwd_file = prompt_env["tmp_path"] / "captured_cwd.txt"
        mock_claude = prompt_env["bin_dir"] / "claude"
        mock_claude.write_text(
            textwrap.dedent(f"""\
            #!/usr/bin/env bash
            pwd > {cwd_file}
            printf '%s' "${{@: -1}}" > {prompt_env["prompt_file"]}
            echo "done"
            exit 0
        """)
        )

        repos = json.dumps([{"url": "https://github.com/org/my-app"}])
        _run(prompt_env, "Fix bugs", extra_env={"FLAUDE_REPOS": repos})
        cwd = cwd_file.read_text().strip()
        assert cwd.endswith("/my-app")
