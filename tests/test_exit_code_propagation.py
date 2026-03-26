"""Tests for AC 5.3 — exit-code and fatal-error propagation through the lifecycle.

Covers:
- extract_exit_code_from_logs() parses [flaude:exit:N] markers
- _is_failure() correctly identifies failure conditions
- run_and_destroy raises MachineExitError on state="failed" even if exit_code is None
- StreamingRun.result() uses log-based exit code when Fly API returns None
- StreamingRun.result() raises for state="failed" with no exit code
- Fatal entrypoint errors (missing env, clone failure) surface with exit_code=1
"""

from __future__ import annotations

import httpx
import pytest
import respx

from flaude.fly_client import FLY_API_BASE
from flaude.machine_config import MachineConfig
from flaude.lifecycle import run_with_logs
from flaude.runner import (
    MachineExitError,
    RunResult,
    _is_failure,
    extract_exit_code_from_logs,
    run_and_destroy,
)

APP = "flaude-test"
TOKEN = "test-fly-token"
MACHINE_ID = "m_exit123"


def _config(**overrides) -> MachineConfig:
    defaults = {
        "claude_code_oauth_token": "oauth-tok",
        "prompt": "Fix the bug",
    }
    defaults.update(overrides)
    return MachineConfig(**defaults)


def _machine_response(*, machine_id: str = MACHINE_ID, state: str = "created") -> dict:
    return {
        "id": machine_id,
        "name": "test-machine",
        "state": state,
        "region": "iad",
        "instance_id": "inst_001",
    }


def _stopped_response(exit_code: int = 0, machine_id: str = MACHINE_ID) -> dict:
    return {
        "id": machine_id,
        "name": "test-machine",
        "state": "stopped",
        "region": "iad",
        "instance_id": "inst_001",
        "events": [{"type": "exit", "status": "stopped", "request": {"exit_event": {"exit_code": exit_code}}}],
    }


def _failed_response(exit_code: int | None = None, machine_id: str = MACHINE_ID) -> dict:
    """Machine that reached the 'failed' state (optionally with exit code)."""
    resp = {
        "id": machine_id,
        "name": "test-machine",
        "state": "failed",
        "region": "iad",
        "instance_id": "inst_001",
    }
    if exit_code is not None:
        resp["events"] = [{"type": "exit", "status": "stopped", "request": {"exit_event": {"exit_code": exit_code}}}]
    return resp


# ---------------------------------------------------------------------------
# extract_exit_code_from_logs — unit tests
# ---------------------------------------------------------------------------


class TestExtractExitCodeFromLogs:
    def test_parses_exit_0(self):
        logs = ["[flaude] Starting execution", "[flaude:exit:0]"]
        assert extract_exit_code_from_logs(logs) == 0

    def test_parses_nonzero_exit(self):
        logs = [
            "[flaude] Starting execution",
            "[flaude] Claude Code exited with code 1",
            "[flaude:exit:1]",
        ]
        assert extract_exit_code_from_logs(logs) == 1

    def test_parses_large_exit_code(self):
        """Exit code 137 (OOM kill) is correctly extracted."""
        logs = ["[flaude:exit:137]"]
        assert extract_exit_code_from_logs(logs) == 137

    def test_returns_none_when_no_marker(self):
        """Returns None when no [flaude:exit:N] marker is present."""
        logs = ["some line", "another line"]
        assert extract_exit_code_from_logs(logs) is None

    def test_returns_none_for_empty_logs(self):
        assert extract_exit_code_from_logs([]) is None

    def test_picks_last_marker_when_multiple(self):
        """Scans in reverse — picks the last (most recent) exit marker."""
        logs = [
            "[flaude:exit:0]",
            "more output",
            "[flaude:exit:1]",
        ]
        # Searching reversed, [flaude:exit:1] appears first
        assert extract_exit_code_from_logs(logs) == 1

    def test_marker_embedded_in_longer_line(self):
        """Marker embedded in a longer line is still found."""
        logs = ["2026-03-25T12:00:00Z stdout [flaude:exit:2]"]
        assert extract_exit_code_from_logs(logs) == 2

    def test_ignores_partial_match(self):
        """Lines without the exact marker pattern are ignored."""
        logs = ["[flaude:exitcode:1]", "[flaude:exit:]", "[flaude:exit: 1]"]
        assert extract_exit_code_from_logs(logs) is None

    def test_exit_code_42(self):
        """Multi-digit exit codes work."""
        logs = ["[flaude] Claude Code exited with code 42", "[flaude:exit:42]"]
        assert extract_exit_code_from_logs(logs) == 42


# ---------------------------------------------------------------------------
# _is_failure — unit tests
# ---------------------------------------------------------------------------


class TestIsFailure:
    def test_zero_exit_stopped_is_not_failure(self):
        assert _is_failure(0, "stopped") is False

    def test_none_exit_stopped_is_not_failure(self):
        assert _is_failure(None, "stopped") is False

    def test_nonzero_exit_is_failure(self):
        assert _is_failure(1, "stopped") is True
        assert _is_failure(137, "stopped") is True

    def test_failed_state_is_failure_even_with_zero_exit(self):
        """state=failed is always a failure regardless of exit code."""
        assert _is_failure(0, "failed") is True

    def test_failed_state_none_exit_is_failure(self):
        """state=failed with None exit_code is still a failure."""
        assert _is_failure(None, "failed") is True

    def test_destroyed_state_none_exit_is_not_failure(self):
        """Destroyed state alone isn't treated as a failure (machine was cleaned up)."""
        assert _is_failure(None, "destroyed") is False

    def test_failed_state_nonzero_exit_is_failure(self):
        assert _is_failure(137, "failed") is True


# ---------------------------------------------------------------------------
# run_and_destroy — failed state propagation
# ---------------------------------------------------------------------------


class TestRunAndDestroyFailedState:
    @respx.mock
    async def test_raises_on_failed_state_with_no_exit_code(self):
        """run_and_destroy raises MachineExitError when state=failed and exit_code=None."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        # wait endpoint fails (common in failed state)
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(500, text="nope"))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_failed_response())
        )
        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        with pytest.raises(MachineExitError) as exc_info:
            await run_and_destroy(APP, _config(), token=TOKEN)

        err = exc_info.value
        assert err.state == "failed"
        assert err.exit_code is None
        assert err.machine_id == MACHINE_ID

    @respx.mock
    async def test_raises_on_failed_state_with_exit_code_137(self):
        """run_and_destroy raises MachineExitError when state=failed and exit_code=137."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(500, text="nope"))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_failed_response(exit_code=137))
        )
        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        with pytest.raises(MachineExitError) as exc_info:
            await run_and_destroy(APP, _config(), token=TOKEN)

        assert exc_info.value.exit_code == 137
        assert exc_info.value.state == "failed"

    @respx.mock
    async def test_no_raise_when_disabled_on_failed_state(self):
        """run_and_destroy doesn't raise when raise_on_failure=False, even for state=failed."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(500, text="nope"))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_failed_response())
        )
        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        result = await run_and_destroy(APP, _config(), token=TOKEN, raise_on_failure=False)
        assert result.state == "failed"


# ---------------------------------------------------------------------------
# StreamingRun.result() — log-based exit code fallback
# ---------------------------------------------------------------------------


class TestStreamingRunLogFallback:
    @respx.mock
    async def test_uses_log_exit_code_when_api_returns_none(self):
        """result() raises MachineExitError with log-parsed exit code when API has none."""
        # API returns no exit code in the machine response
        no_exit_response = {
            "id": MACHINE_ID,
            "name": "test-machine",
            "state": "stopped",
            "region": "iad",
            "instance_id": "inst_001",
            # No "events" key — Fly API didn't populate exit code
        }

        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=no_exit_response)
        )
        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        streaming = await run_with_logs(
            APP, _config(), token=TOKEN, item_timeout=0.5
        )

        # Simulate logs arriving that include the exit marker
        await streaming._collector.push(MACHINE_ID, "[flaude] Starting execution")
        await streaming._collector.push(MACHINE_ID, "[flaude] Claude Code exited with code 1")
        await streaming._collector.push(MACHINE_ID, "[flaude:exit:1]")

        async for _ in streaming:
            pass

        with pytest.raises(MachineExitError) as exc_info:
            await streaming.result()

        err = exc_info.value
        # Log-derived exit code should be used since API returned None
        assert err.exit_code == 1
        assert err.machine_id == MACHINE_ID
        assert "[flaude:exit:1]" in err.logs

    @respx.mock
    async def test_log_exit_code_zero_does_not_raise(self):
        """result() does not raise when log-parsed exit code is 0."""
        no_exit_response = {
            "id": MACHINE_ID,
            "name": "test-machine",
            "state": "stopped",
            "region": "iad",
            "instance_id": "inst_001",
        }

        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=no_exit_response)
        )
        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        streaming = await run_with_logs(
            APP, _config(), token=TOKEN, item_timeout=0.5
        )

        await streaming._collector.push(MACHINE_ID, "[flaude:exit:0]")

        async for _ in streaming:
            pass

        # exit 0 should NOT raise
        result = await streaming.result()
        assert result.machine_id == MACHINE_ID

    @respx.mock
    async def test_api_exit_code_takes_precedence_over_log(self):
        """When API provides an exit code, it's used; log-based is just a fallback."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        # API clearly says exit_code=2
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response(exit_code=2))
        )
        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        streaming = await run_with_logs(
            APP, _config(), token=TOKEN, item_timeout=0.5
        )

        # Log says exit:99 but API says exit_code=2 — API wins
        await streaming._collector.push(MACHINE_ID, "[flaude:exit:99]")

        async for _ in streaming:
            pass

        with pytest.raises(MachineExitError) as exc_info:
            await streaming.result()

        # API-provided exit code (2) takes precedence
        assert exc_info.value.exit_code == 2

    @respx.mock
    async def test_raises_on_failed_state_with_log_exit_code(self):
        """result() raises MachineExitError for state=failed with log-based exit code."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(500, text="nope"))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_failed_response())
        )
        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        streaming = await run_with_logs(
            APP, _config(), token=TOKEN, item_timeout=0.5
        )

        # Logs contain exit marker even though state is "failed"
        await streaming._collector.push(MACHINE_ID, "fatal: out of memory")
        await streaming._collector.push(MACHINE_ID, "[flaude:exit:137]")

        async for _ in streaming:
            pass

        with pytest.raises(MachineExitError) as exc_info:
            await streaming.result()

        err = exc_info.value
        assert err.state == "failed"
        assert err.exit_code == 137
        assert "fatal: out of memory" in err.logs

    @respx.mock
    async def test_raises_on_failed_state_no_log_exit_code(self):
        """result() raises MachineExitError for state=failed even without log marker."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(500, text="nope"))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_failed_response())
        )
        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        streaming = await run_with_logs(
            APP, _config(), token=TOKEN, item_timeout=0.5
        )

        # No exit marker in logs (machine was abruptly killed before it could write one)
        await streaming._collector.push(MACHINE_ID, "container was killed")

        async for _ in streaming:
            pass

        with pytest.raises(MachineExitError) as exc_info:
            await streaming.result()

        err = exc_info.value
        assert err.state == "failed"
        assert err.exit_code is None  # neither API nor logs had it
        assert "container was killed" in err.logs


# ---------------------------------------------------------------------------
# End-to-end: fatal entrypoint errors surface correctly
# ---------------------------------------------------------------------------


class TestFatalEntrypointErrors:
    """The entrypoint emits [flaude:exit:1] for fatal setup errors.

    These are surfaced as MachineExitError with exit_code=1 and the log
    lines showing what went wrong.
    """

    @respx.mock
    async def test_missing_oauth_token_surfaces_exit_1(self):
        """Missing CLAUDE_CODE_OAUTH_TOKEN → exit 1 from entrypoint."""
        # The entrypoint exits with code 1 for missing required vars.
        # This is reflected either via the Fly API or the [flaude:exit:1] marker.
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        # Fly API doesn't report exit code
        no_exit_response = {
            "id": MACHINE_ID,
            "name": "test-machine",
            "state": "stopped",
            "region": "iad",
            "instance_id": "inst_001",
        }
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=no_exit_response)
        )
        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        streaming = await run_with_logs(
            APP, _config(), token=TOKEN, item_timeout=0.5
        )

        # Simulate the log output that the real entrypoint would produce
        await streaming._collector.push(
            MACHINE_ID, "[flaude:error] CLAUDE_CODE_OAUTH_TOKEN is not set"
        )
        await streaming._collector.push(MACHINE_ID, "[flaude:exit:1]")

        logs = []
        async for line in streaming:
            logs.append(line)

        with pytest.raises(MachineExitError) as exc_info:
            await streaming.result()

        err = exc_info.value
        assert err.exit_code == 1
        assert any("CLAUDE_CODE_OAUTH_TOKEN" in l for l in err.logs)

    @respx.mock
    async def test_missing_prompt_surfaces_exit_1(self):
        """Missing FLAUDE_PROMPT → exit 1 from entrypoint."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(
                200,
                json={"id": MACHINE_ID, "state": "stopped", "region": "iad"},
            )
        )
        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        streaming = await run_with_logs(
            APP, _config(), token=TOKEN, item_timeout=0.5
        )

        await streaming._collector.push(
            MACHINE_ID, "[flaude:error] FLAUDE_PROMPT is not set"
        )
        await streaming._collector.push(MACHINE_ID, "[flaude:exit:1]")

        async for _ in streaming:
            pass

        with pytest.raises(MachineExitError) as exc_info:
            await streaming.result()

        assert exc_info.value.exit_code == 1
        assert any("FLAUDE_PROMPT" in l for l in exc_info.value.logs)
