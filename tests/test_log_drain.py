"""Tests for flaude.log_drain — log drain receiver and log collector."""

from __future__ import annotations

import asyncio
import json

import pytest

from flaude.log_drain import (
    LogCollector,
    LogDrainServer,
    LogEntry,
    async_iter_queue,
    drain_queue,
    parse_log_entry,
    parse_ndjson,
)


# ---------------------------------------------------------------------------
# parse_ndjson
# ---------------------------------------------------------------------------


class TestParseNdjson:
    def test_single_line(self):
        body = json.dumps({"message": "hello"}).encode()
        result = parse_ndjson(body)
        assert len(result) == 1
        assert result[0]["message"] == "hello"

    def test_multiple_lines(self):
        lines = [
            json.dumps({"message": "line1"}),
            json.dumps({"message": "line2"}),
            json.dumps({"message": "line3"}),
        ]
        body = "\n".join(lines).encode()
        result = parse_ndjson(body)
        assert len(result) == 3
        assert [r["message"] for r in result] == ["line1", "line2", "line3"]

    def test_skips_blank_lines(self):
        body = b'{"a":1}\n\n{"b":2}\n\n'
        result = parse_ndjson(body)
        assert len(result) == 2

    def test_skips_malformed_lines(self):
        body = b'{"good":1}\nnot-json\n{"also_good":2}\n'
        result = parse_ndjson(body)
        assert len(result) == 2
        assert result[0]["good"] == 1
        assert result[1]["also_good"] == 2

    def test_empty_body(self):
        assert parse_ndjson(b"") == []
        assert parse_ndjson(b"\n\n") == []

    def test_non_dict_json_skipped(self):
        body = b'[1,2,3]\n{"ok":true}\n'
        result = parse_ndjson(body)
        assert len(result) == 1
        assert result[0]["ok"] is True


# ---------------------------------------------------------------------------
# parse_log_entry
# ---------------------------------------------------------------------------


class TestParseLogEntry:
    def test_fly_app_instance_format(self):
        """Fly log drain uses fly.app.instance for machine ID."""
        raw = {
            "fly": {"app": {"instance": "m-abc123", "name": "myapp"}},
            "message": "hello world",
            "stream": "stdout",
            "timestamp": "2024-01-01T00:00:00Z",
        }
        entry = parse_log_entry(raw)
        assert entry is not None
        assert entry.machine_id == "m-abc123"
        assert entry.message == "hello world"
        assert entry.stream == "stdout"
        assert entry.app_name == "myapp"
        assert entry.timestamp == "2024-01-01T00:00:00Z"

    def test_fly_machine_id_format(self):
        raw = {
            "fly": {"machine": {"id": "m-xyz789"}},
            "message": "test line",
            "stream": "stderr",
        }
        entry = parse_log_entry(raw)
        assert entry is not None
        assert entry.machine_id == "m-xyz789"
        assert entry.stream == "stderr"

    def test_flat_instance_field(self):
        raw = {"instance": "m-flat", "message": "flat format"}
        entry = parse_log_entry(raw)
        assert entry is not None
        assert entry.machine_id == "m-flat"

    def test_flat_machine_id_field(self):
        raw = {"machine_id": "m-direct", "log": "using log field"}
        entry = parse_log_entry(raw)
        assert entry is not None
        assert entry.machine_id == "m-direct"
        assert entry.message == "using log field"

    def test_missing_machine_id_returns_none(self):
        raw = {"message": "no machine id"}
        assert parse_log_entry(raw) is None

    def test_default_stream_is_stdout(self):
        raw = {"instance": "m-1", "message": "no stream"}
        entry = parse_log_entry(raw)
        assert entry is not None
        assert entry.stream == "stdout"

    def test_msg_field_alias(self):
        raw = {"instance": "m-1", "msg": "using msg"}
        entry = parse_log_entry(raw)
        assert entry is not None
        assert entry.message == "using msg"

    def test_raw_preserved(self):
        raw = {"instance": "m-1", "message": "test", "extra": "data"}
        entry = parse_log_entry(raw)
        assert entry is not None
        assert entry.raw == raw


# ---------------------------------------------------------------------------
# LogCollector
# ---------------------------------------------------------------------------


class TestLogCollector:
    async def test_subscribe_creates_queue(self):
        collector = LogCollector()
        q = await collector.subscribe("m-1")
        assert isinstance(q, asyncio.Queue)

    async def test_subscribe_returns_same_queue(self):
        collector = LogCollector()
        q1 = await collector.subscribe("m-1")
        q2 = await collector.subscribe("m-1")
        assert q1 is q2

    async def test_push_delivers_to_subscriber(self):
        collector = LogCollector()
        q = await collector.subscribe("m-1")
        await collector.push("m-1", "hello")
        assert q.get_nowait() == "hello"

    async def test_push_to_unknown_machine_is_silent(self):
        collector = LogCollector()
        # Should not raise
        await collector.push("unknown", "dropped")

    async def test_finish_sends_sentinel(self):
        collector = LogCollector()
        q = await collector.subscribe("m-1")
        await collector.push("m-1", "line1")
        await collector.finish("m-1")
        assert q.get_nowait() == "line1"
        assert q.get_nowait() is None  # sentinel

    async def test_finish_removes_from_registry(self):
        collector = LogCollector()
        await collector.subscribe("m-1")
        await collector.finish("m-1")
        assert "m-1" not in collector.machine_ids

    async def test_finish_unknown_is_silent(self):
        collector = LogCollector()
        await collector.finish("unknown")  # Should not raise

    async def test_finish_all(self):
        collector = LogCollector()
        q1 = await collector.subscribe("m-1")
        q2 = await collector.subscribe("m-2")
        await collector.push("m-1", "a")
        await collector.push("m-2", "b")
        await collector.finish_all()
        assert q1.get_nowait() == "a"
        assert q1.get_nowait() is None
        assert q2.get_nowait() == "b"
        assert q2.get_nowait() is None

    async def test_machine_ids(self):
        collector = LogCollector()
        await collector.subscribe("m-1")
        await collector.subscribe("m-2")
        assert sorted(collector.machine_ids) == ["m-1", "m-2"]

    async def test_multiple_machines_isolated(self):
        collector = LogCollector()
        q1 = await collector.subscribe("m-1")
        q2 = await collector.subscribe("m-2")
        await collector.push("m-1", "for-m1")
        await collector.push("m-2", "for-m2")
        assert q1.get_nowait() == "for-m1"
        assert q2.get_nowait() == "for-m2"
        assert q1.qsize() == 0
        assert q2.qsize() == 0


# ---------------------------------------------------------------------------
# drain_queue / async_iter_queue
# ---------------------------------------------------------------------------


class TestDrainQueue:
    async def test_drain_collects_all_lines(self):
        q: asyncio.Queue[str | None] = asyncio.Queue()
        await q.put("line1")
        await q.put("line2")
        await q.put("line3")
        await q.put(None)  # sentinel
        result = await drain_queue(q)
        assert result == ["line1", "line2", "line3"]

    async def test_drain_empty_returns_empty(self):
        q: asyncio.Queue[str | None] = asyncio.Queue()
        await q.put(None)
        result = await drain_queue(q)
        assert result == []

    async def test_drain_timeout(self):
        q: asyncio.Queue[str | None] = asyncio.Queue()
        # No sentinel — should timeout
        with pytest.raises(asyncio.TimeoutError):
            await drain_queue(q, timeout=0.1)


class TestAsyncIterQueue:
    async def test_iterates_until_sentinel(self):
        q: asyncio.Queue[str | None] = asyncio.Queue()
        await q.put("a")
        await q.put("b")
        await q.put("c")
        await q.put(None)
        lines = [line async for line in async_iter_queue(q)]
        assert lines == ["a", "b", "c"]

    async def test_empty_iteration(self):
        q: asyncio.Queue[str | None] = asyncio.Queue()
        await q.put(None)
        lines = [line async for line in async_iter_queue(q)]
        assert lines == []


# ---------------------------------------------------------------------------
# LogDrainServer (integration tests with real HTTP)
# ---------------------------------------------------------------------------


class TestLogDrainServer:
    async def test_server_starts_and_stops(self):
        collector = LogCollector()
        server = LogDrainServer(collector, port=0)
        await server.start()
        assert server.actual_port is not None
        assert server.actual_port > 0
        assert server.url is not None
        await server.stop()
        assert server.actual_port is None

    async def test_receives_log_entries(self):
        collector = LogCollector()
        q = await collector.subscribe("m-test-1")
        server = LogDrainServer(collector, port=0)
        await server.start()

        try:
            # Send NDJSON log entries like Fly would
            entries = [
                {"fly": {"app": {"instance": "m-test-1"}}, "message": "hello", "stream": "stdout"},
                {"fly": {"app": {"instance": "m-test-1"}}, "message": "world", "stream": "stdout"},
            ]
            body = "\n".join(json.dumps(e) for e in entries).encode()
            await _http_post(server.actual_port, body)

            # Allow processing
            await asyncio.sleep(0.1)

            assert q.get_nowait() == "hello"
            assert q.get_nowait() == "world"
        finally:
            await server.stop()

    async def test_filters_stderr_by_default(self):
        collector = LogCollector()
        q = await collector.subscribe("m-test-2")
        server = LogDrainServer(collector, port=0)
        await server.start()

        try:
            entries = [
                {"fly": {"app": {"instance": "m-test-2"}}, "message": "stdout-line", "stream": "stdout"},
                {"fly": {"app": {"instance": "m-test-2"}}, "message": "stderr-line", "stream": "stderr"},
            ]
            body = "\n".join(json.dumps(e) for e in entries).encode()
            await _http_post(server.actual_port, body)
            await asyncio.sleep(0.1)

            assert q.get_nowait() == "stdout-line"
            assert q.qsize() == 0  # stderr was filtered
        finally:
            await server.stop()

    async def test_includes_stderr_when_configured(self):
        collector = LogCollector()
        q = await collector.subscribe("m-test-3")
        server = LogDrainServer(collector, port=0, include_stderr=True)
        await server.start()

        try:
            entries = [
                {"fly": {"app": {"instance": "m-test-3"}}, "message": "out", "stream": "stdout"},
                {"fly": {"app": {"instance": "m-test-3"}}, "message": "err", "stream": "stderr"},
            ]
            body = "\n".join(json.dumps(e) for e in entries).encode()
            await _http_post(server.actual_port, body)
            await asyncio.sleep(0.1)

            assert q.get_nowait() == "out"
            assert q.get_nowait() == "err"
        finally:
            await server.stop()

    async def test_routes_to_correct_machine(self):
        collector = LogCollector()
        q1 = await collector.subscribe("m-a")
        q2 = await collector.subscribe("m-b")
        server = LogDrainServer(collector, port=0)
        await server.start()

        try:
            entries = [
                {"fly": {"app": {"instance": "m-a"}}, "message": "for-a", "stream": "stdout"},
                {"fly": {"app": {"instance": "m-b"}}, "message": "for-b", "stream": "stdout"},
                {"fly": {"app": {"instance": "m-a"}}, "message": "for-a-2", "stream": "stdout"},
            ]
            body = "\n".join(json.dumps(e) for e in entries).encode()
            await _http_post(server.actual_port, body)
            await asyncio.sleep(0.1)

            assert q1.get_nowait() == "for-a"
            assert q1.get_nowait() == "for-a-2"
            assert q2.get_nowait() == "for-b"
        finally:
            await server.stop()

    async def test_unsubscribed_machine_logs_dropped(self):
        collector = LogCollector()
        server = LogDrainServer(collector, port=0)
        await server.start()

        try:
            entries = [
                {"fly": {"app": {"instance": "m-unknown"}}, "message": "dropped", "stream": "stdout"},
            ]
            body = "\n".join(json.dumps(e) for e in entries).encode()
            # Should not raise
            await _http_post(server.actual_port, body)
            await asyncio.sleep(0.1)
        finally:
            await server.stop()

    async def test_end_to_end_with_drain(self):
        """Full flow: subscribe → receive logs → finish → drain."""
        collector = LogCollector()
        q = await collector.subscribe("m-e2e")
        server = LogDrainServer(collector, port=0)
        await server.start()

        try:
            # Simulate Fly sending logs
            entries = [
                {"fly": {"app": {"instance": "m-e2e"}}, "message": f"line-{i}", "stream": "stdout"}
                for i in range(5)
            ]
            body = "\n".join(json.dumps(e) for e in entries).encode()
            await _http_post(server.actual_port, body)
            await asyncio.sleep(0.1)

            # Signal completion
            await collector.finish("m-e2e")

            # Drain should collect all lines
            lines = await drain_queue(q, timeout=2.0)
            assert lines == [f"line-{i}" for i in range(5)]
        finally:
            await server.stop()


async def _http_post(port: int, body: bytes) -> None:
    """Send a raw HTTP POST to the log drain server."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    request = (
        f"POST /logs HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Content-Type: application/x-ndjson\r\n"
        f"\r\n"
    ).encode() + body
    writer.write(request)
    await writer.drain()
    # Read response
    await asyncio.wait_for(reader.read(1024), timeout=5.0)
    writer.close()
    await writer.wait_closed()
