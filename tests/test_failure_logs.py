"""Tests for AC 8 — on failure, exceptions contain available logs."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from flaude.fly_client import FLY_API_BASE
from flaude.log_drain import LogCollector, LogDrainServer
from flaude.machine_config import MachineConfig
from flaude.lifecycle import StreamingRun, run_with_logs
from flaude.runner import MachineExitError, run_and_destroy

APP = "flaude-test"
TOKEN = "test-fly-token"
MACHINE_ID = "m_fail123"


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
        "events": [{"type": "exit", "status": {"exit_code": exit_code}}],
    }


# ---------------------------------------------------------------------------
# MachineExitError — logs attribute
# ---------------------------------------------------------------------------


class TestMachineExitErrorLogs:
    def test_error_has_empty_logs_by_default(self):
        """MachineExitError has empty logs when none provided."""
        err = MachineExitError("m1", exit_code=1, state="stopped")
        assert err.logs == []
        assert err.machine_id == "m1"
        assert err.exit_code == 1

    def test_error_carries_logs(self):
        """MachineExitError stores provided log lines."""
        logs = ["Starting...", "Error: something broke", "Exiting"]
        err = MachineExitError("m1", exit_code=1, state="stopped", logs=logs)
        assert err.logs == logs
        assert len(err.logs) == 3

    def test_error_message_includes_log_tail(self):
        """The exception message includes a tail of the logs for quick debugging."""
        logs = ["line1", "line2", "fatal error occurred"]
        err = MachineExitError("m1", exit_code=1, state="stopped", logs=logs)
        msg = str(err)
        assert "fatal error occurred" in msg
        assert "3 log lines" in msg

    def test_error_message_truncates_long_logs(self):
        """When there are >20 log lines, message shows 'Last 20 of N'."""
        logs = [f"line {i}" for i in range(50)]
        err = MachineExitError("m1", exit_code=1, state="stopped", logs=logs)
        msg = str(err)
        assert "Last 20 of 50" in msg
        # Should show the last line, not the first
        assert "line 49" in msg
        assert "line 0" not in msg

    def test_error_message_no_logs_section_when_empty(self):
        """When no logs, message doesn't include a log section."""
        err = MachineExitError("m1", exit_code=1, state="stopped")
        msg = str(err)
        assert "log lines" not in msg
        assert "code=1" in msg

    def test_error_none_logs_becomes_empty_list(self):
        """Passing None for logs normalizes to empty list."""
        err = MachineExitError("m1", exit_code=1, state="stopped", logs=None)
        assert err.logs == []


# ---------------------------------------------------------------------------
# run_and_destroy — raises MachineExitError (without logs, no drain)
# ---------------------------------------------------------------------------


class TestRunAndDestroyErrorLogs:
    @respx.mock
    async def test_raises_with_empty_logs(self):
        """run_and_destroy raises MachineExitError with empty logs (no log drain)."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response(exit_code=1))
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.delete(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true").mock(
            return_value=httpx.Response(200, json={})
        )

        with pytest.raises(MachineExitError) as exc_info:
            await run_and_destroy(APP, _config(), token=TOKEN)

        assert exc_info.value.exit_code == 1
        assert exc_info.value.logs == []


# ---------------------------------------------------------------------------
# StreamingRun — failure raises with collected logs
# ---------------------------------------------------------------------------


class TestStreamingRunFailureLogs:
    @respx.mock
    async def test_result_raises_with_collected_logs(self):
        """StreamingRun.result() raises MachineExitError with collected logs on failure."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response(exit_code=1))
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.delete(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true").mock(
            return_value=httpx.Response(200, json={})
        )

        streaming = await run_with_logs(
            APP, _config(), token=TOKEN, item_timeout=0.5
        )

        # Simulate log lines arriving
        await streaming._collector.push(MACHINE_ID, "Cloning repo...")
        await streaming._collector.push(MACHINE_ID, "Running claude...")
        await streaming._collector.push(MACHINE_ID, "Error: authentication failed")

        # Iterate to collect the logs
        lines = []
        async for line in streaming:
            lines.append(line)

        # result() should raise with the collected logs
        with pytest.raises(MachineExitError) as exc_info:
            await streaming.result()

        err = exc_info.value
        assert err.exit_code == 1
        assert err.machine_id == MACHINE_ID
        assert len(err.logs) == 3
        assert "Cloning repo..." in err.logs
        assert "Error: authentication failed" in err.logs
        assert "authentication failed" in str(err)

    @respx.mock
    async def test_result_no_raise_when_disabled(self):
        """StreamingRun.result(raise_on_failure=False) returns RunResult even on failure."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response(exit_code=1))
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.delete(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true").mock(
            return_value=httpx.Response(200, json={})
        )

        streaming = await run_with_logs(
            APP, _config(), token=TOKEN, item_timeout=0.5
        )

        # Collect logs via iteration
        async for _ in streaming:
            pass

        # Should NOT raise
        result = await streaming.result(raise_on_failure=False)
        assert result.exit_code == 1
        assert result.machine_id == MACHINE_ID

    @respx.mock
    async def test_result_no_raise_on_success(self):
        """StreamingRun.result() does not raise when exit code is 0."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response(exit_code=0))
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.delete(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true").mock(
            return_value=httpx.Response(200, json={})
        )

        streaming = await run_with_logs(
            APP, _config(), token=TOKEN, item_timeout=0.5
        )

        async for _ in streaming:
            pass

        result = await streaming.result()
        assert result.exit_code == 0

    @respx.mock
    async def test_collected_logs_property(self):
        """collected_logs property returns a copy of collected log lines."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response(exit_code=0))
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.delete(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true").mock(
            return_value=httpx.Response(200, json={})
        )

        streaming = await run_with_logs(
            APP, _config(), token=TOKEN, item_timeout=0.5
        )

        await streaming._collector.push(MACHINE_ID, "log line 1")
        await streaming._collector.push(MACHINE_ID, "log line 2")

        # Read one line
        line = await streaming.__anext__()
        assert line == "log line 1"

        # collected_logs should have 1 line so far
        assert streaming.collected_logs == ["log line 1"]

        # Modifying the returned list shouldn't affect internal state
        streaming.collected_logs.append("fake")
        assert len(streaming.collected_logs) == 1

        # Finish iteration
        async for _ in streaming:
            pass

        result = await streaming.result()
        assert result.exit_code == 0
        assert streaming.collected_logs == ["log line 1", "log line 2"]

    @respx.mock
    async def test_result_raises_without_iterating(self):
        """result() raises with empty logs if caller never iterated."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response(exit_code=1))
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.delete(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true").mock(
            return_value=httpx.Response(200, json={})
        )

        streaming = await run_with_logs(APP, _config(), token=TOKEN)

        # Don't iterate — call result() directly
        with pytest.raises(MachineExitError) as exc_info:
            await streaming.result()

        # Logs are empty because we never iterated
        assert exc_info.value.logs == []
        assert exc_info.value.exit_code == 1

    @respx.mock
    async def test_context_manager_raises_with_logs(self):
        """When used as context manager, result() still raises with logs."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response(exit_code=2))
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.delete(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true").mock(
            return_value=httpx.Response(200, json={})
        )

        async with await run_with_logs(
            APP, _config(), token=TOKEN, item_timeout=0.5
        ) as streaming:
            await streaming._collector.push(MACHINE_ID, "working...")
            await streaming._collector.push(MACHINE_ID, "crash!")

            async for _ in streaming:
                pass

            with pytest.raises(MachineExitError) as exc_info:
                await streaming.result()

            assert exc_info.value.exit_code == 2
            assert "crash!" in exc_info.value.logs
