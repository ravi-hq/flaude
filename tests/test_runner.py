"""Tests for flaude.runner — automatic machine destruction and run lifecycle."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from flaude.fly_client import FLY_API_BASE, FlyAPIError
from flaude.machine_config import MachineConfig
from flaude.runner import (
    MachineExitError,
    RunResult,
    _cleanup_machine,
    _extract_exit_code,
    run,
    run_and_destroy,
    wait_for_machine_exit,
)

APP = "flaude-test"
TOKEN = "test-fly-token"
MACHINE_ID = "m_abc123"


def _machine_config(**overrides) -> MachineConfig:
    defaults = {
        "claude_code_oauth_token": "oauth-tok",
        "prompt": "Fix the bug",
    }
    defaults.update(overrides)
    return MachineConfig(**defaults)


def _machine_response(
    *,
    machine_id: str = MACHINE_ID,
    state: str = "created",
) -> dict:
    return {
        "id": machine_id,
        "name": "test-machine",
        "state": state,
        "region": "iad",
        "instance_id": "inst_001",
    }


def _machine_stopped_response(exit_code: int = 0) -> dict:
    return {
        "id": MACHINE_ID,
        "name": "test-machine",
        "state": "stopped",
        "region": "iad",
        "instance_id": "inst_001",
        "events": [
            {"type": "exit", "status": "stopped", "request": {"exit_event": {"exit_code": exit_code}}},
        ],
    }


# ---------------------------------------------------------------------------
# _extract_exit_code
# ---------------------------------------------------------------------------


def test_extract_exit_code_from_events():
    data = {"events": [{"type": "exit", "status": "stopped", "request": {"exit_event": {"exit_code": 0}}}]}
    assert _extract_exit_code(data) == 0


def test_extract_exit_code_nonzero():
    data = {"events": [{"type": "exit", "status": "stopped", "request": {"exit_event": {"exit_code": 1}}}]}
    assert _extract_exit_code(data) == 1


def test_extract_exit_code_monitor_event():
    data = {"events": [{"type": "exit", "status": "stopped", "request": {"monitor_event": {"exit_event": {"exit_code": 42}}}}]}
    assert _extract_exit_code(data) == 42


def test_extract_exit_code_missing():
    assert _extract_exit_code({}) is None
    assert _extract_exit_code({"events": []}) is None


# ---------------------------------------------------------------------------
# wait_for_machine_exit — via wait endpoint
# ---------------------------------------------------------------------------


@respx.mock
async def test_wait_uses_wait_endpoint():
    """wait_for_machine_exit uses the /wait endpoint and then fetches state."""
    respx.get(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
    ).mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
        return_value=httpx.Response(200, json=_machine_stopped_response(0))
    )

    state, exit_code = await wait_for_machine_exit(APP, MACHINE_ID, token=TOKEN)
    assert state == "stopped"
    assert exit_code == 0


@respx.mock
async def test_wait_falls_back_to_polling():
    """When /wait fails, falls back to polling GET /machines/{id}."""
    respx.get(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
    ).mock(return_value=httpx.Response(500, text="not available"))

    # First poll: still running; second poll: stopped
    get_route = respx.get(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}"
    ).mock(
        side_effect=[
            httpx.Response(200, json=_machine_response(state="started")),
            httpx.Response(200, json=_machine_stopped_response(0)),
        ]
    )

    state, exit_code = await wait_for_machine_exit(
        APP, MACHINE_ID, token=TOKEN, poll_interval=0.01
    )
    assert state == "stopped"
    assert exit_code == 0
    assert get_route.call_count == 2


@respx.mock
async def test_wait_handles_404_as_destroyed():
    """If the machine is already gone (404), treat as destroyed."""
    respx.get(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
    ).mock(return_value=httpx.Response(500, text="nope"))
    respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
        return_value=httpx.Response(404, text="not found")
    )

    state, exit_code = await wait_for_machine_exit(
        APP, MACHINE_ID, token=TOKEN, poll_interval=0.01
    )
    assert state == "destroyed"
    assert exit_code is None


# ---------------------------------------------------------------------------
# _cleanup_machine
# ---------------------------------------------------------------------------


@respx.mock
async def test_cleanup_machine_stops_and_destroys():
    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
    ).mock(return_value=httpx.Response(200, json={}))
    respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
    ).mock(return_value=httpx.Response(200, json={}))

    result = await _cleanup_machine(APP, MACHINE_ID, token=TOKEN)
    assert result is True


@respx.mock
async def test_cleanup_machine_continues_on_stop_failure():
    """Cleanup continues to destroy even if stop fails."""
    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
    ).mock(return_value=httpx.Response(500, text="fail"))
    destroy_route = respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
    ).mock(return_value=httpx.Response(200, json={}))

    result = await _cleanup_machine(APP, MACHINE_ID, token=TOKEN)
    assert result is True
    assert destroy_route.called


@respx.mock
async def test_cleanup_machine_returns_false_on_destroy_failure():
    """If destroy also fails, returns False."""
    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
    ).mock(return_value=httpx.Response(200, json={}))
    respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
    ).mock(return_value=httpx.Response(500, text="fail"))

    result = await _cleanup_machine(APP, MACHINE_ID, token=TOKEN)
    assert result is False


# ---------------------------------------------------------------------------
# run — guaranteed cleanup via try/finally
# ---------------------------------------------------------------------------


@respx.mock
async def test_run_creates_waits_and_destroys_on_success():
    """Full success path: create → wait → destroy."""
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        return_value=httpx.Response(200, json=_machine_response())
    )
    respx.get(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
    ).mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
        return_value=httpx.Response(200, json=_machine_stopped_response(0))
    )
    stop_route = respx.post(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
    ).mock(return_value=httpx.Response(200, json={}))
    destroy_route = respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
    ).mock(return_value=httpx.Response(200, json={}))

    result = await run(APP, _machine_config(), token=TOKEN)

    assert result.machine_id == MACHINE_ID
    assert result.exit_code == 0
    assert result.state == "stopped"
    # Cleanup was called
    assert destroy_route.called


@respx.mock
async def test_run_destroys_on_nonzero_exit():
    """Machine is destroyed even when Claude Code exits with non-zero code."""
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        return_value=httpx.Response(200, json=_machine_response())
    )
    respx.get(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
    ).mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
        return_value=httpx.Response(200, json=_machine_stopped_response(1))
    )
    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
    ).mock(return_value=httpx.Response(200, json={}))
    destroy_route = respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
    ).mock(return_value=httpx.Response(200, json={}))

    result = await run(APP, _machine_config(), token=TOKEN)

    assert result.exit_code == 1
    assert destroy_route.called


@respx.mock
async def test_run_destroys_on_wait_exception():
    """Machine is destroyed even when wait_for_machine_exit raises."""
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        return_value=httpx.Response(200, json=_machine_response())
    )
    # Wait endpoint fails with 500, polling also fails with 500
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

    with pytest.raises(FlyAPIError):
        await run(APP, _machine_config(), token=TOKEN)

    # Machine MUST still be destroyed
    assert destroy_route.called


@respx.mock
async def test_run_no_cleanup_if_create_fails():
    """If machine creation fails, there's nothing to clean up."""
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        return_value=httpx.Response(422, text="bad config")
    )

    with pytest.raises(FlyAPIError):
        await run(APP, _machine_config(), token=TOKEN)


@respx.mock
async def test_run_destroys_on_cancellation():
    """Machine is destroyed even when the task is cancelled."""
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        return_value=httpx.Response(200, json=_machine_response())
    )
    # Wait hangs forever (simulated by a long delay), gets cancelled
    respx.get(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
    ).mock(return_value=httpx.Response(500, text="nope"))

    call_count = 0

    async def slow_poll(request):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            # Simulate hang
            await asyncio.sleep(100)
        return httpx.Response(200, json=_machine_response(state="started"))

    respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
        side_effect=slow_poll
    )
    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
    ).mock(return_value=httpx.Response(200, json={}))
    destroy_route = respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
    ).mock(return_value=httpx.Response(200, json={}))

    task = asyncio.create_task(
        run(APP, _machine_config(), token=TOKEN, wait_timeout=3600)
    )
    # Give the task time to start polling
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert destroy_route.called


# ---------------------------------------------------------------------------
# run_and_destroy — raises on failure
# ---------------------------------------------------------------------------


@respx.mock
async def test_run_and_destroy_raises_on_nonzero():
    """run_and_destroy raises MachineExitError on non-zero exit."""
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        return_value=httpx.Response(200, json=_machine_response())
    )
    respx.get(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
    ).mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
        return_value=httpx.Response(200, json=_machine_stopped_response(1))
    )
    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
    ).mock(return_value=httpx.Response(200, json={}))
    respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
    ).mock(return_value=httpx.Response(200, json={}))

    with pytest.raises(MachineExitError) as exc_info:
        await run_and_destroy(APP, _machine_config(), token=TOKEN)

    assert exc_info.value.exit_code == 1
    assert exc_info.value.machine_id == MACHINE_ID


@respx.mock
async def test_run_and_destroy_no_raise_when_disabled():
    """run_and_destroy returns result when raise_on_failure=False."""
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        return_value=httpx.Response(200, json=_machine_response())
    )
    respx.get(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
    ).mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
        return_value=httpx.Response(200, json=_machine_stopped_response(1))
    )
    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
    ).mock(return_value=httpx.Response(200, json={}))
    respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
    ).mock(return_value=httpx.Response(200, json={}))

    result = await run_and_destroy(
        APP, _machine_config(), token=TOKEN, raise_on_failure=False
    )
    assert result.exit_code == 1
    assert result.machine_id == MACHINE_ID


# ---------------------------------------------------------------------------
# Automatic destruction — comprehensive scenarios
# ---------------------------------------------------------------------------


@respx.mock
async def test_run_destroys_on_failed_machine_state():
    """Machine is destroyed when it exits in 'failed' state (not just non-zero exit)."""
    failed_response = {
        "id": MACHINE_ID,
        "name": "test-machine",
        "state": "failed",
        "region": "iad",
        "instance_id": "inst_001",
        "events": [
            {"type": "exit", "status": "stopped", "request": {"exit_event": {"exit_code": 137}}},
        ],
    }

    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        return_value=httpx.Response(200, json=_machine_response())
    )
    respx.get(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
    ).mock(return_value=httpx.Response(500, text="nope"))
    respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
        return_value=httpx.Response(200, json=failed_response)
    )
    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
    ).mock(return_value=httpx.Response(200, json={}))
    destroy_route = respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
    ).mock(return_value=httpx.Response(200, json={}))

    result = await run(APP, _machine_config(), token=TOKEN)

    assert result.state == "failed"
    assert result.exit_code == 137
    assert destroy_route.called


@respx.mock
async def test_cleanup_handles_both_stop_and_destroy_api_errors():
    """When both stop and destroy return API errors, cleanup handles gracefully."""
    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
    ).mock(return_value=httpx.Response(500, text="stop server error"))
    respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
    ).mock(return_value=httpx.Response(500, text="destroy server error"))

    # _cleanup_machine should NOT raise — it returns False
    result = await _cleanup_machine(APP, MACHINE_ID, token=TOKEN)
    assert result is False


@respx.mock
async def test_cleanup_handles_network_error_on_stop():
    """Network-level errors during stop are caught and cleanup continues to destroy."""
    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
    ).mock(side_effect=httpx.ConnectError("connection refused"))
    destroy_route = respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
    ).mock(return_value=httpx.Response(200, json={}))

    result = await _cleanup_machine(APP, MACHINE_ID, token=TOKEN)
    assert result is True
    assert destroy_route.called


@respx.mock
async def test_cleanup_handles_network_error_on_destroy():
    """Network-level errors during destroy result in False (potential orphan)."""
    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
    ).mock(return_value=httpx.Response(200, json={}))
    respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
    ).mock(side_effect=httpx.ConnectError("connection refused"))

    result = await _cleanup_machine(APP, MACHINE_ID, token=TOKEN)
    assert result is False


@respx.mock
async def test_cleanup_handles_timeout_on_destroy():
    """Timeout during destroy is handled gracefully."""
    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
    ).mock(return_value=httpx.Response(200, json={}))
    respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
    ).mock(side_effect=httpx.ReadTimeout("timed out"))

    result = await _cleanup_machine(APP, MACHINE_ID, token=TOKEN)
    assert result is False


@respx.mock
async def test_run_destroys_after_arbitrary_exception(monkeypatch):
    """Machine is destroyed even when an unexpected (non-API) exception occurs."""
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        return_value=httpx.Response(200, json=_machine_response())
    )

    # Patch wait_for_machine_exit to raise an arbitrary exception
    async def _boom(*args, **kwargs):
        raise RuntimeError("unexpected internal error")

    import flaude.runner as runner_mod

    monkeypatch.setattr(runner_mod, "wait_for_machine_exit", _boom)

    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
    ).mock(return_value=httpx.Response(200, json={}))
    destroy_route = respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
    ).mock(return_value=httpx.Response(200, json={}))

    with pytest.raises(RuntimeError, match="unexpected internal error"):
        await run(APP, _machine_config(), token=TOKEN)

    # Machine MUST still be destroyed via finally block
    assert destroy_route.called


@respx.mock
async def test_run_cleanup_failure_does_not_mask_original_exception():
    """If cleanup fails, the original exception still propagates (not the cleanup error)."""
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        return_value=httpx.Response(200, json=_machine_response())
    )
    # Wait endpoint fails
    respx.get(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
    ).mock(return_value=httpx.Response(500, text="broken"))
    respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
        return_value=httpx.Response(500, text="broken")
    )
    # Stop fails
    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
    ).mock(return_value=httpx.Response(500, text="stop broken"))
    # Destroy also fails
    respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
    ).mock(return_value=httpx.Response(500, text="destroy broken"))

    # The original FlyAPIError from polling should propagate, not cleanup errors
    with pytest.raises(FlyAPIError) as exc_info:
        await run(APP, _machine_config(), token=TOKEN)

    assert exc_info.value.status_code == 500


@respx.mock
async def test_run_and_destroy_still_cleans_up_on_exit_error():
    """run_and_destroy destroys the machine even when raising MachineExitError."""
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        return_value=httpx.Response(200, json=_machine_response())
    )
    respx.get(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
    ).mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
        return_value=httpx.Response(200, json=_machine_stopped_response(2))
    )
    stop_route = respx.post(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
    ).mock(return_value=httpx.Response(200, json={}))
    destroy_route = respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
    ).mock(return_value=httpx.Response(200, json={}))

    with pytest.raises(MachineExitError) as exc_info:
        await run_and_destroy(APP, _machine_config(), token=TOKEN)

    assert exc_info.value.exit_code == 2
    # Cleanup happened before MachineExitError was raised
    assert stop_route.called
    assert destroy_route.called
