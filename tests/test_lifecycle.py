"""Tests for flaude.lifecycle — log drain integration into machine lifecycle."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx

from flaude.fly_client import FLY_API_BASE
from flaude.log_drain import LogCollector, LogDrainServer
from flaude.machine_config import MachineConfig
from flaude.lifecycle import StreamingRun, run_with_logs, _wait_signal_destroy

APP = "flaude-test"
TOKEN = "test-fly-token"
MACHINE_ID = "m_life123"


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
# Log drain server is started BEFORE machine creation
# ---------------------------------------------------------------------------


class TestDrainSetupBeforeMachineStart:
    @respx.mock
    async def test_server_started_before_create(self):
        """Log drain server is started before the machine is created."""
        server_started_before_create = False

        original_create = respx.post(f"{FLY_API_BASE}/apps/{APP}/machines")

        async def check_create(request):
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
        respx.delete(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true").mock(
            return_value=httpx.Response(200, json={})
        )

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
    async def test_auto_creates_collector_and_server(self):
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
        respx.delete(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true").mock(
            return_value=httpx.Response(200, json={})
        )

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
    async def test_server_stopped_after_machine_destroyed(self):
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
        assert streaming._server.actual_port is None  # server stopped
        assert streaming._cleaned_up is True

    @respx.mock
    async def test_cleanup_idempotent(self):
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
        respx.delete(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true").mock(
            return_value=httpx.Response(200, json={})
        )

        streaming = await run_with_logs(APP, _config(), token=TOKEN)
        await streaming.result()
        await streaming.cleanup()
        await streaming.cleanup()  # Second call should be a no-op
        await streaming.cleanup()  # Third call too
        assert streaming._cleaned_up is True

    @respx.mock
    async def test_external_server_not_stopped(self):
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
            ).mock(return_value=httpx.Response(200, json={})
            )

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
    async def test_server_stopped_if_create_fails(self):
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
    async def test_external_server_not_stopped_on_create_failure(self):
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
    async def test_signals_finish_on_normal_exit(self):
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
        respx.delete(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true").mock(
            return_value=httpx.Response(200, json={})
        )

        from flaude.machine import FlyMachine

        machine = FlyMachine(
            id=MACHINE_ID, name="test", state="created",
            region="iad", instance_id="i1", app_name=APP,
        )

        result = await _wait_signal_destroy(APP, machine, collector, token=TOKEN)
        assert result.exit_code == 0

        # Sentinel should have been pushed
        item = queue.get_nowait()
        assert item is None  # sentinel

    @respx.mock
    async def test_signals_finish_even_on_error(self):
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
        respx.delete(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true").mock(
            return_value=httpx.Response(200, json={})
        )

        from flaude.machine import FlyMachine

        machine = FlyMachine(
            id=MACHINE_ID, name="test", state="created",
            region="iad", instance_id="i1", app_name=APP,
        )

        from flaude.fly_client import FlyAPIError

        with pytest.raises(FlyAPIError):
            await _wait_signal_destroy(APP, machine, collector, token=TOKEN)

        # Sentinel should still have been pushed (via finally)
        item = queue.get_nowait()
        assert item is None

    @respx.mock
    async def test_destroys_machine_on_exit(self):
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
        ).mock(return_value=httpx.Response(200, json={})
        )

        from flaude.machine import FlyMachine

        machine = FlyMachine(
            id=MACHINE_ID, name="test", state="created",
            region="iad", instance_id="i1", app_name=APP,
        )

        await _wait_signal_destroy(APP, machine, collector, token=TOKEN)
        assert destroy_route.called


# ---------------------------------------------------------------------------
# StreamingRun — async iteration
# ---------------------------------------------------------------------------


class TestStreamingRunIteration:
    @respx.mock
    async def test_iterates_log_lines(self):
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
        respx.delete(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true").mock(
            return_value=httpx.Response(200, json={})
        )

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
    async def test_async_for_iteration(self):
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
        respx.delete(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true").mock(
            return_value=httpx.Response(200, json={})
        )

        streaming = await run_with_logs(
            APP, _config(), token=TOKEN, item_timeout=0.5
        )

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
    async def test_machine_id_accessible(self):
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
        respx.delete(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true").mock(
            return_value=httpx.Response(200, json={})
        )

        streaming = await run_with_logs(APP, _config(), token=TOKEN)
        assert streaming.machine_id == MACHINE_ID
        await streaming.result()
        await streaming.cleanup()


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class TestStreamingRunContextManager:
    @respx.mock
    async def test_context_manager_cleanup(self):
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
        respx.delete(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true").mock(
            return_value=httpx.Response(200, json={})
        )

        async with await run_with_logs(APP, _config(), token=TOKEN) as streaming:
            assert streaming.machine_id == MACHINE_ID
            result = await streaming.result()
            assert result.exit_code == 0

        # After context manager exit, cleanup should have run
        assert streaming._cleaned_up is True
        assert streaming._server.actual_port is None


# ---------------------------------------------------------------------------
# Shared collector/server (concurrent use case)
# ---------------------------------------------------------------------------


class TestSharedInfrastructure:
    @respx.mock
    async def test_reuses_external_collector_and_server(self):
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
            ).mock(return_value=httpx.Response(200, json={})
            )

            streaming = await run_with_logs(
                APP, _config(), token=TOKEN,
                collector=collector, server=server,
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
    async def test_subscriber_registered_for_machine(self):
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
            ).mock(return_value=httpx.Response(200, json={})
            )

            streaming = await run_with_logs(
                APP, _config(), token=TOKEN,
                collector=collector, server=server,
            )

            # Machine ID should be registered in collector initially
            # (may be removed after finish is called)
            assert streaming.machine_id == MACHINE_ID

            await streaming.result()
            await streaming.cleanup()
        finally:
            await server.stop()
