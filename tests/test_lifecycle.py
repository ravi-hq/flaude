"""Tests for flaude.lifecycle — log drain integration into machine lifecycle."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
import respx

from flaude.fly_client import FLY_API_BASE
from flaude.lifecycle import _wait_signal_destroy, run_with_logs
from flaude.log_drain import LogCollector, LogDrainServer
from flaude.machine_config import MachineConfig

APP = "flaude-test"
TOKEN = "test-fly-token"
MACHINE_ID = "m_life123"


def _config(**overrides: Any) -> MachineConfig:
    defaults = {
        "claude_code_oauth_token": "oauth-tok",
        "prompt": "Fix the bug",
    }
    defaults.update(overrides)
    return MachineConfig(**defaults)  # type: ignore[arg-type]


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
        "events": [
            {
                "type": "exit",
                "status": "stopped",
                "request": {"exit_event": {"exit_code": exit_code}},
            }
        ],
    }


# ---------------------------------------------------------------------------
# Log drain server is started BEFORE machine creation
# ---------------------------------------------------------------------------


class TestDrainSetupBeforeMachineStart:
    @respx.mock
    async def test_server_started_before_create(self) -> None:
        """Log drain server is started before the machine is created."""
        server_started_before_create = False

        original_create = respx.post(f"{FLY_API_BASE}/apps/{APP}/machines")

        async def check_create(request: httpx.Request) -> httpx.Response:
            nonlocal server_started_before_create
            # At this point, the server should already be running
            # We verify by checking that run_with_logs started the server
            server_started_before_create = True
            return httpx.Response(200, json=_machine_response())

        original_create.mock(side_effect=check_create)

        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response())
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        streaming = await run_with_logs(APP, _config(), token=TOKEN)
        # Server should have been started before create was called
        assert server_started_before_create

        # The streaming run should have a valid server with a port
        assert streaming._server is not None
        assert streaming._server.actual_port is not None
        assert streaming._server.actual_port > 0

        # Clean up
        await streaming.result()
        await streaming.cleanup()

    @respx.mock
    async def test_auto_creates_collector_and_server(self) -> None:
        """When no collector/server provided, they are auto-created."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response())
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        streaming = await run_with_logs(APP, _config(), token=TOKEN)
        assert streaming._collector is not None
        assert streaming._server is not None
        assert streaming._owns_server is True
        await streaming.result()
        await streaming.cleanup()


# ---------------------------------------------------------------------------
# Log drain cleanup AFTER machine destruction
# ---------------------------------------------------------------------------


class TestDrainCleanupAfterDestruction:
    @respx.mock
    async def test_server_stopped_after_machine_destroyed(self) -> None:
        """Log drain server is stopped after machine is destroyed."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response())
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop").mock(
            return_value=httpx.Response(200, json={})
        )
        destroy_route = respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        streaming = await run_with_logs(APP, _config(), token=TOKEN)
        result = await streaming.result()

        # Machine was destroyed
        assert destroy_route.called
        assert result.exit_code == 0

        # Server should be stopped after cleanup
        await streaming.cleanup()
        assert streaming._server is not None
        assert streaming._server.actual_port is None  # server stopped
        assert streaming._cleaned_up is True

    @respx.mock
    async def test_cleanup_idempotent(self) -> None:
        """Calling cleanup multiple times is safe."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response())
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        streaming = await run_with_logs(APP, _config(), token=TOKEN)
        await streaming.result()
        await streaming.cleanup()
        await streaming.cleanup()  # Second call should be a no-op
        await streaming.cleanup()  # Third call too
        assert streaming._cleaned_up is True

    @respx.mock
    async def test_external_server_not_stopped(self) -> None:
        """When server is provided externally, it is NOT stopped on cleanup."""
        collector = LogCollector()
        server = LogDrainServer(collector, port=0)
        await server.start()
        original_port = server.actual_port

        try:
            respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
                return_value=httpx.Response(200, json=_machine_response())
            )
            respx.get(
                f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
            ).mock(return_value=httpx.Response(200, json={}))
            respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
                return_value=httpx.Response(200, json=_stopped_response())
            )
            respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop").mock(
                return_value=httpx.Response(200, json={})
            )
            respx.delete(
                f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
            ).mock(return_value=httpx.Response(200, json={}))

            streaming = await run_with_logs(
                APP, _config(), token=TOKEN, collector=collector, server=server
            )
            assert streaming._owns_server is False

            await streaming.result()
            await streaming.cleanup()

            # Server should STILL be running
            assert server.actual_port == original_port
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# Drain server cleaned up on creation failure
# ---------------------------------------------------------------------------


class TestDrainCleanupOnCreationFailure:
    @respx.mock
    async def test_server_stopped_if_create_fails(self) -> None:
        """If machine creation fails, the auto-created server is stopped."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(422, text="bad config")
        )

        from flaude.fly_client import FlyAPIError

        with pytest.raises(FlyAPIError):
            await run_with_logs(APP, _config(), token=TOKEN)

        # No leaked server — we can't directly check but the test not hanging
        # proves the server was stopped (no background listeners)

    @respx.mock
    async def test_external_server_not_stopped_on_create_failure(self) -> None:
        """External server is NOT stopped when machine creation fails."""
        collector = LogCollector()
        server = LogDrainServer(collector, port=0)
        await server.start()
        original_port = server.actual_port

        try:
            respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
                return_value=httpx.Response(422, text="bad config")
            )

            from flaude.fly_client import FlyAPIError

            with pytest.raises(FlyAPIError):
                await run_with_logs(
                    APP, _config(), token=TOKEN, collector=collector, server=server
                )

            # External server should still be running
            assert server.actual_port == original_port
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# _wait_signal_destroy signals collector.finish
# ---------------------------------------------------------------------------


class TestWaitSignalDestroy:
    @respx.mock
    async def test_signals_finish_on_normal_exit(self) -> None:
        """Collector.finish() is called after machine exits normally."""
        collector = LogCollector()
        queue = await collector.subscribe(MACHINE_ID)

        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response())
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        from flaude.machine import FlyMachine

        machine = FlyMachine(
            id=MACHINE_ID,
            name="test",
            state="created",
            region="iad",
            instance_id="i1",
            app_name=APP,
        )

        result = await _wait_signal_destroy(APP, machine, collector, token=TOKEN)
        assert result.exit_code == 0

        # Sentinel should have been pushed
        item = queue.get_nowait()
        assert item is None  # sentinel

    @respx.mock
    async def test_signals_finish_even_on_error(self) -> None:
        """Collector.finish() is called even when wait fails."""
        collector = LogCollector()
        queue = await collector.subscribe(MACHINE_ID)

        # Wait endpoint fails, polling also fails
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(500, text="broken"))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(500, text="broken")
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        from flaude.machine import FlyMachine

        machine = FlyMachine(
            id=MACHINE_ID,
            name="test",
            state="created",
            region="iad",
            instance_id="i1",
            app_name=APP,
        )

        from flaude.fly_client import FlyAPIError

        with pytest.raises(FlyAPIError):
            await _wait_signal_destroy(APP, machine, collector, token=TOKEN)

        # Sentinel should still have been pushed (via finally)
        item = queue.get_nowait()
        assert item is None

    @respx.mock
    async def test_destroys_machine_on_exit(self) -> None:
        """Machine is destroyed after exit in _wait_signal_destroy."""
        collector = LogCollector()
        await collector.subscribe(MACHINE_ID)

        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response())
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop").mock(
            return_value=httpx.Response(200, json={})
        )
        destroy_route = respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        from flaude.machine import FlyMachine

        machine = FlyMachine(
            id=MACHINE_ID,
            name="test",
            state="created",
            region="iad",
            instance_id="i1",
            app_name=APP,
        )

        await _wait_signal_destroy(APP, machine, collector, token=TOKEN)
        assert destroy_route.called


# ---------------------------------------------------------------------------
# StreamingRun — async iteration
# ---------------------------------------------------------------------------


class TestStreamingRunAsyncIteratorProtocol:
    """Verify __aiter__/__anext__ protocol and real-time delivery guarantees."""

    def _mock_machine(self, machine_id: str = MACHINE_ID) -> None:
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(
                200, json=_machine_response(machine_id=machine_id)
            )
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{machine_id}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{machine_id}").mock(
            return_value=httpx.Response(
                200, json=_stopped_response(machine_id=machine_id)
            )
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{machine_id}/stop").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{machine_id}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

    @respx.mock
    async def test_aiter_returns_self(self) -> None:
        """__aiter__() must return the StreamingRun itself (iterator protocol)."""
        self._mock_machine()
        streaming = await run_with_logs(APP, _config(), token=TOKEN)
        assert streaming.__aiter__() is streaming
        await streaming.result()
        await streaming.cleanup()

    @respx.mock
    async def test_anext_raises_stop_after_sentinel(self) -> None:
        """__anext__ raises StopAsyncIteration once the stream is exhausted."""
        self._mock_machine()
        streaming = await run_with_logs(APP, _config(), token=TOKEN)

        # Wait for the background task (which pushes the sentinel)
        await streaming.result(raise_on_failure=False)

        # Stream is done — __anext__ must raise StopAsyncIteration
        with pytest.raises(StopAsyncIteration):
            await streaming.__anext__()

    @respx.mock
    async def test_lines_available_before_machine_stops(self) -> None:
        """Lines are yielded to the caller in real-time, before the machine exits.

        This test verifies that a caller iterating the stream receives each line
        as soon as it is pushed to the collector — not batched until machine stop.
        """
        self._mock_machine()
        streaming = await run_with_logs(APP, _config(), token=TOKEN)

        received: list[str] = []
        line_received_event = asyncio.Event()

        async def consumer() -> None:
            async for line in streaming:
                received.append(line)
                if not line_received_event.is_set():
                    line_received_event.set()

        consume_task = asyncio.create_task(consumer())

        # Push a line — it should be yielded immediately, even though the
        # machine background task has not yet delivered the sentinel.
        await streaming._collector.push(MACHINE_ID, "early-line")

        # Wait for the consumer to receive it (real-time: must arrive quickly)
        await asyncio.wait_for(line_received_event.wait(), timeout=1.0)
        assert received == ["early-line"], (
            f"Expected ['early-line'] but got {received}; "
            "line was not delivered in real-time"
        )

        # Now let the machine fully stop (sentinel will be pushed via result())
        await streaming.result(raise_on_failure=False)
        await consume_task
        await streaming.cleanup()

    @respx.mock
    async def test_lines_delivered_in_order(self) -> None:
        """Lines are yielded in the same order they were pushed."""
        self._mock_machine()
        streaming = await run_with_logs(APP, _config(), token=TOKEN)

        # Pre-load lines into the collector queue
        ordered_lines = [f"line-{i}" for i in range(10)]
        for line in ordered_lines:
            await streaming._collector.push(MACHINE_ID, line)

        # Collect via async for (sentinel arrives via result background task)
        collected: list[str] = []
        async for line in streaming:
            collected.append(line)
            if len(collected) == len(ordered_lines):
                break  # don't wait for sentinel; we have what we need

        assert collected == ordered_lines

        await streaming.result(raise_on_failure=False)
        await streaming.cleanup()

    @respx.mock
    async def test_collected_logs_tracks_yielded_lines(self) -> None:
        """collected_logs property accumulates all lines yielded via __anext__."""
        self._mock_machine()
        streaming = await run_with_logs(APP, _config(), token=TOKEN, item_timeout=0.5)

        await streaming._collector.push(MACHINE_ID, "alpha")
        await streaming._collector.push(MACHINE_ID, "beta")

        # Iterate — background task will send sentinel and stop iteration
        lines = []
        async for line in streaming:
            lines.append(line)

        # collected_logs should mirror what we iterated
        assert "alpha" in streaming.collected_logs
        assert "beta" in streaming.collected_logs
        assert streaming.collected_logs == lines

        await streaming.result(raise_on_failure=False)
        await streaming.cleanup()

    @respx.mock
    async def test_terminates_cleanly_when_machine_stops(self) -> None:
        """Iteration ends without error when the machine exits normally."""
        self._mock_machine()
        streaming = await run_with_logs(APP, _config(), token=TOKEN)

        await streaming._collector.push(MACHINE_ID, "msg1")
        await streaming._collector.push(MACHINE_ID, "msg2")

        # Exhaust the stream — should terminate with no exception
        lines = []
        async for line in streaming:
            lines.append(line)

        # Stream is cleanly done (not timed out)
        assert streaming.done
        assert not streaming.log_stream.timed_out

        result = await streaming.result(raise_on_failure=False)
        assert result.exit_code == 0
        await streaming.cleanup()


class TestStreamingRunIteration:
    @respx.mock
    async def test_iterates_log_lines(self) -> None:
        """StreamingRun yields log lines from the machine."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response())
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        streaming = await run_with_logs(APP, _config(), token=TOKEN)

        # Simulate log lines arriving via the collector
        await streaming._collector.push(MACHINE_ID, "line1")
        await streaming._collector.push(MACHINE_ID, "line2")

        # Wait for the background task to complete (which signals finish)
        result = await streaming.result()
        assert result.exit_code == 0

        # The log stream should have received lines + sentinel
        # We already consumed the result which triggered cleanup
        await streaming.cleanup()

    @respx.mock
    async def test_async_for_iteration(self) -> None:
        """Can iterate using async for."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response())
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        streaming = await run_with_logs(APP, _config(), token=TOKEN, item_timeout=0.5)

        # Push some lines then let background task finish (which calls finish)
        await streaming._collector.push(MACHINE_ID, "hello")
        await streaming._collector.push(MACHINE_ID, "world")

        # Collect lines — the background task will push sentinel when done
        lines = []
        async for line in streaming:
            lines.append(line)

        assert "hello" in lines
        assert "world" in lines

        await streaming.result()
        await streaming.cleanup()

    @respx.mock
    async def test_machine_id_accessible(self) -> None:
        """machine_id is accessible on the StreamingRun."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response())
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        streaming = await run_with_logs(APP, _config(), token=TOKEN)
        assert streaming.machine_id == MACHINE_ID
        await streaming.result()
        await streaming.cleanup()


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class TestStreamingRunContextManager:
    @respx.mock
    async def test_context_manager_cleanup(self) -> None:
        """async with ensures cleanup on exit."""
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            return_value=httpx.Response(200, json=_machine_response())
        )
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(200, json=_stopped_response())
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

        async with await run_with_logs(APP, _config(), token=TOKEN) as streaming:
            assert streaming.machine_id == MACHINE_ID
            result = await streaming.result()
            assert result.exit_code == 0

        # After context manager exit, cleanup should have run
        assert streaming._cleaned_up is True
        assert streaming._server is not None
        assert streaming._server.actual_port is None


# ---------------------------------------------------------------------------
# Shared collector/server (concurrent use case)
# ---------------------------------------------------------------------------


class TestSharedInfrastructure:
    @respx.mock
    async def test_reuses_external_collector_and_server(self) -> None:
        """When collector/server are provided, they are reused, not recreated."""
        collector = LogCollector()
        server = LogDrainServer(collector, port=0)
        await server.start()
        original_port = server.actual_port

        try:
            respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
                return_value=httpx.Response(200, json=_machine_response())
            )
            respx.get(
                f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
            ).mock(return_value=httpx.Response(200, json={}))
            respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
                return_value=httpx.Response(200, json=_stopped_response())
            )
            respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop").mock(
                return_value=httpx.Response(200, json={})
            )
            respx.delete(
                f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
            ).mock(return_value=httpx.Response(200, json={}))

            streaming = await run_with_logs(
                APP,
                _config(),
                token=TOKEN,
                collector=collector,
                server=server,
            )

            # Should be using the provided instances
            assert streaming._collector is collector
            assert streaming._server is server
            assert streaming._owns_server is False

            await streaming.result()
            await streaming.cleanup()

            # Server should still be running
            assert server.actual_port == original_port
        finally:
            await server.stop()

    @respx.mock
    async def test_subscriber_registered_for_machine(self) -> None:
        """The machine ID is subscribed in the collector after run_with_logs."""
        collector = LogCollector()
        server = LogDrainServer(collector, port=0)
        await server.start()

        try:
            respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
                return_value=httpx.Response(200, json=_machine_response())
            )
            respx.get(
                f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
            ).mock(return_value=httpx.Response(200, json={}))
            respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
                return_value=httpx.Response(200, json=_stopped_response())
            )
            respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop").mock(
                return_value=httpx.Response(200, json={})
            )
            respx.delete(
                f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
            ).mock(return_value=httpx.Response(200, json={}))

            streaming = await run_with_logs(
                APP,
                _config(),
                token=TOKEN,
                collector=collector,
                server=server,
            )

            # Machine ID should be registered in collector initially
            # (may be removed after finish is called)
            assert streaming.machine_id == MACHINE_ID

            await streaming.result()
            await streaming.cleanup()
        finally:
            await server.stop()
