"""Tests verifying machine destruction on both success and failure.

This module provides focused tests confirming that Fly machines are *always*
destroyed after Claude Code execution — regardless of whether execution:

* Completes successfully (exit code 0)
* Fails with a non-zero Claude Code exit code
* Fails because the wait/polling API returns errors
* Fails because of unexpected internal exceptions
* Is cancelled via asyncio task cancellation

Both the bare :func:`flaude.runner.run` path **and** the log-streaming
:func:`flaude.lifecycle.run_with_logs` path are covered so that no machine
path can leak orphaned resources.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from flaude.fly_client import FLY_API_BASE, FlyAPIError
from flaude.machine_config import MachineConfig
from flaude.runner import MachineExitError, RunResult, run, run_and_destroy

APP = "flaude-cleanup-test"
TOKEN = "test-fly-token"
MACHINE_ID = "m_cleanup001"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(**overrides) -> MachineConfig:
    defaults = {
        "claude_code_oauth_token": "oauth-tok",
        "prompt": "Write a hello-world script",
    }
    defaults.update(overrides)
    return MachineConfig(**defaults)


def _machine_response(
    machine_id: str = MACHINE_ID, state: str = "created"
) -> dict:
    return {
        "id": machine_id,
        "name": f"machine-{machine_id}",
        "state": state,
        "region": "iad",
        "instance_id": f"inst_{machine_id}",
    }


def _stopped_response(machine_id: str = MACHINE_ID, exit_code: int = 0) -> dict:
    return {
        "id": machine_id,
        "name": f"machine-{machine_id}",
        "state": "stopped",
        "region": "iad",
        "instance_id": f"inst_{machine_id}",
        "events": [{"type": "exit", "status": "stopped", "request": {"exit_event": {"exit_code": exit_code}}}],
    }


def _failed_response(machine_id: str = MACHINE_ID, exit_code: int = 137) -> dict:
    return {
        "id": machine_id,
        "name": f"machine-{machine_id}",
        "state": "failed",
        "region": "iad",
        "instance_id": f"inst_{machine_id}",
        "events": [{"type": "exit", "status": "stopped", "request": {"exit_event": {"exit_code": exit_code}}}],
    }


# ---------------------------------------------------------------------------
# runner.run() — destruction after successful Claude Code completion
# ---------------------------------------------------------------------------


class TestDestroyOnSuccess:
    """Machine is destroyed when Claude Code exits cleanly (exit code 0)."""

    @respx.mock
    async def test_destroy_called_on_zero_exit(self):
        """DELETE /machines/{id} is called after exit_code=0."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response(exit_code=0))
        )
        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        destroy_route = respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        result = await run(APP, _config(), token=TOKEN)

        assert result.exit_code == 0
        assert result.state == "stopped"
        assert destroy_route.called, "Machine must be destroyed on successful exit"

    @respx.mock
    async def test_stop_then_destroy_on_success(self):
        """Both stop and destroy are called in sequence on success."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response(exit_code=0))
        )
        stop_route = respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        destroy_route = respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        await run(APP, _config(), token=TOKEN)

        assert stop_route.called, "Machine must be stopped before destroy"
        assert destroy_route.called, "Machine must be destroyed after stop"

    @respx.mock
    async def test_run_and_destroy_cleans_up_on_success(self):
        """run_and_destroy also destroys the machine on exit code 0."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response(exit_code=0))
        )
        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        destroy_route = respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        result = await run_and_destroy(APP, _config(), token=TOKEN)

        assert result.exit_code == 0
        assert destroy_route.called, "run_and_destroy must destroy machine on success"


# ---------------------------------------------------------------------------
# runner.run() — destruction after Claude Code execution failure (non-zero exit)
# ---------------------------------------------------------------------------


class TestDestroyOnNonZeroExit:
    """Machine is destroyed even when Claude Code exits with a non-zero code."""

    @respx.mock
    async def test_destroy_on_exit_code_1(self):
        """Machine is destroyed when Claude Code exits with code 1."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response(exit_code=1))
        )
        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        destroy_route = respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        # run() returns result without raising for non-zero
        result = await run(APP, _config(), token=TOKEN)

        assert result.exit_code == 1
        assert destroy_route.called, "Machine must be destroyed on non-zero exit"

    @respx.mock
    async def test_destroy_on_exit_code_2(self):
        """Machine is destroyed when Claude Code exits with code 2."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response(exit_code=2))
        )
        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        destroy_route = respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        result = await run(APP, _config(), token=TOKEN)

        assert result.exit_code == 2
        assert destroy_route.called

    @respx.mock
    async def test_destroy_before_raising_machine_exit_error(self):
        """run_and_destroy destroys the machine BEFORE raising MachineExitError."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response(exit_code=1))
        )
        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        destroy_route = respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        with pytest.raises(MachineExitError) as exc_info:
            await run_and_destroy(APP, _config(), token=TOKEN)

        assert exc_info.value.exit_code == 1
        assert exc_info.value.machine_id == MACHINE_ID
        # Cleanup MUST have happened even though exception was raised
        assert destroy_route.called, (
            "Machine must be destroyed before MachineExitError is raised"
        )

    @respx.mock
    async def test_destroy_on_machine_failed_state(self):
        """Machine in 'failed' state (OOM-killed, etc.) is destroyed."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        # Wait endpoint unavailable — fall through to polling
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(500, text="unavailable"))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(
                200, json=_failed_response(exit_code=137)
            )
        )
        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        destroy_route = respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        result = await run(APP, _config(), token=TOKEN)

        assert result.state == "failed"
        assert result.exit_code == 137
        assert destroy_route.called, "Machine must be destroyed even in 'failed' state"


# ---------------------------------------------------------------------------
# runner.run() — destruction after exception during Claude Code execution
# ---------------------------------------------------------------------------


class TestDestroyOnException:
    """Machine is destroyed when exceptions occur during execution."""

    @respx.mock
    async def test_destroy_on_api_error_during_wait(self):
        """Machine is destroyed when the wait API returns persistent errors."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        # Both wait and polling endpoints fail
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(500, text="internal error"))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(500, text="internal error")
        )
        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        destroy_route = respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        with pytest.raises(FlyAPIError):
            await run(APP, _config(), token=TOKEN)

        assert destroy_route.called, "Machine must be destroyed after API error"

    @respx.mock
    async def test_destroy_on_arbitrary_exception(self, monkeypatch):
        """Machine is destroyed when an unexpected non-API exception occurs."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )

        async def _raise_runtime(*args, **kwargs):
            raise RuntimeError("simulated internal crash")

        import flaude.runner as runner_mod

        monkeypatch.setattr(runner_mod, "wait_for_machine_exit", _raise_runtime)

        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        destroy_route = respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        with pytest.raises(RuntimeError, match="simulated internal crash"):
            await run(APP, _config(), token=TOKEN)

        assert destroy_route.called, "Machine must be destroyed after arbitrary exception"

    @respx.mock
    async def test_destroy_on_network_error_during_wait(self, monkeypatch):
        """Machine is destroyed when a network error interrupts the wait."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )

        async def _raise_network(*args, **kwargs):
            raise httpx.ConnectError("connection refused")

        import flaude.runner as runner_mod

        monkeypatch.setattr(runner_mod, "wait_for_machine_exit", _raise_network)

        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        destroy_route = respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        with pytest.raises(httpx.ConnectError):
            await run(APP, _config(), token=TOKEN)

        assert destroy_route.called, "Machine must be destroyed after network error"

    @respx.mock
    async def test_destroy_on_task_cancellation(self):
        """Machine is destroyed when the asyncio task is cancelled."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        # Fallback wait endpoint fails, polling hangs (simulates long-running machine)
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(500, text="unavailable"))

        poll_call_count = 0

        async def _slow_poll(request):
            nonlocal poll_call_count
            poll_call_count += 1
            if poll_call_count >= 2:
                await asyncio.sleep(100)  # hang to simulate long-running
            return httpx.Response(200, json=_machine_response(state="started"))

        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            side_effect=_slow_poll
        )
        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        destroy_route = respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        task = asyncio.create_task(run(APP, _config(), token=TOKEN, wait_timeout=3600))
        # Let the task start and begin polling
        await asyncio.sleep(0.05)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert destroy_route.called, "Machine must be destroyed on task cancellation"

    @respx.mock
    async def test_no_machine_to_destroy_if_create_fails(self):
        """When machine creation fails, no destroy call is made (nothing to destroy)."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(422, text="bad machine config")
        )
        # No delete route registered — if DELETE is called the test will fail
        # because respx raises an error for unexpected requests

        with pytest.raises(FlyAPIError) as exc_info:
            await run(APP, _config(), token=TOKEN)

        assert exc_info.value.status_code == 422

    @respx.mock
    async def test_original_exception_propagates_even_if_cleanup_fails(self):
        """The original exception is raised even if cleanup itself fails."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        # Wait fails
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(500, text="broken"))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(500, text="broken")
        )
        # Cleanup also fails
        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(500, text="stop broken"))
        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(500, text="destroy broken"))

        # The original FlyAPIError from polling must propagate
        with pytest.raises(FlyAPIError) as exc_info:
            await run(APP, _config(), token=TOKEN)

        assert exc_info.value.status_code == 500, (
            "Original exception must propagate; cleanup failure must not mask it"
        )


# ---------------------------------------------------------------------------
# lifecycle.run_with_logs() — destruction through the streaming path
# ---------------------------------------------------------------------------


class TestDestroyViaLifecycle:
    """Machine is destroyed when using the log-streaming lifecycle path."""

    @respx.mock
    async def test_lifecycle_destroys_on_success(self):
        """run_with_logs destroys machine after clean exit."""
        from flaude.lifecycle import run_with_logs

        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response(exit_code=0))
        )
        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        destroy_route = respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        streaming = await run_with_logs(APP, _config(), token=TOKEN)
        result = await streaming.result()
        await streaming.cleanup()

        assert result.exit_code == 0
        assert destroy_route.called, "Machine must be destroyed via lifecycle on success"

    @respx.mock
    async def test_lifecycle_destroys_on_nonzero_exit(self):
        """run_with_logs destroys machine after non-zero exit code."""
        from flaude.lifecycle import run_with_logs

        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response(exit_code=1))
        )
        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        destroy_route = respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        streaming = await run_with_logs(APP, _config(), token=TOKEN)
        with pytest.raises(MachineExitError):
            await streaming.result(raise_on_failure=True)
        await streaming.cleanup()

        assert destroy_route.called, "Machine must be destroyed on non-zero exit via lifecycle"

    @respx.mock
    async def test_lifecycle_destroys_even_when_result_raises(self):
        """Machine is destroyed even when result() raises MachineExitError."""
        from flaude.lifecycle import run_with_logs

        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response(exit_code=42))
        )
        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        destroy_route = respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        streaming = await run_with_logs(APP, _config(), token=TOKEN)
        # result() raises but triggers cleanup internally
        with pytest.raises(MachineExitError) as exc_info:
            await streaming.result(raise_on_failure=True)

        assert exc_info.value.exit_code == 42
        # Cleanup was triggered by result()
        assert destroy_route.called, "Machine must be destroyed even when result() raises"

    @respx.mock
    async def test_lifecycle_destroys_on_api_error_during_wait(self):
        """Machine is destroyed via lifecycle when wait API fails."""
        from flaude.lifecycle import run_with_logs

        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        # Both wait paths fail
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(500, text="broken"))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(500, text="broken")
        )
        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        destroy_route = respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        streaming = await run_with_logs(APP, _config(), token=TOKEN)
        with pytest.raises(FlyAPIError):
            await streaming.result()
        await streaming.cleanup()

        assert destroy_route.called, "Machine must be destroyed after API error via lifecycle"

    @respx.mock
    async def test_lifecycle_context_manager_destroys_on_success(self):
        """async with run_with_logs destroys machine on clean exit."""
        from flaude.lifecycle import run_with_logs

        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response(exit_code=0))
        )
        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        destroy_route = respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        async with await run_with_logs(APP, _config(), token=TOKEN) as streaming:
            result = await streaming.result()

        assert result.exit_code == 0
        assert destroy_route.called, "Machine must be destroyed via context manager"

    @respx.mock
    async def test_lifecycle_context_manager_destroys_on_exception(self):
        """async with run_with_logs destroys machine when an exception escapes the body."""
        from flaude.lifecycle import run_with_logs

        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response(exit_code=0))
        )
        respx.post(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
        ).mock(return_value=httpx.Response(200, json={}))
        destroy_route = respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        with pytest.raises(ValueError, match="simulated caller error"):
            async with await run_with_logs(APP, _config(), token=TOKEN) as streaming:
                # Context manager __aexit__ always calls cleanup — which stops the
                # server.  The background task (which destroys the machine) is
                # independent and will fire after the context exits.
                await streaming.result()
                raise ValueError("simulated caller error")

        assert destroy_route.called, (
            "Machine must be destroyed even when caller raises inside context manager"
        )
