"""Tests for flaude.log_drain.LogStream — async iterator wrapper."""

from __future__ import annotations

import asyncio

import pytest

from flaude.log_drain import LogCollector, LogStream


# ---------------------------------------------------------------------------
# Basic iteration
# ---------------------------------------------------------------------------


class TestLogStreamBasic:
    async def test_yields_lines_until_sentinel(self):
        q: asyncio.Queue[str | None] = asyncio.Queue()
        await q.put("line1")
        await q.put("line2")
        await q.put("line3")
        await q.put(None)

        stream = LogStream(q)
        lines = []
        async for line in stream:
            lines.append(line)

        assert lines == ["line1", "line2", "line3"]
        assert stream.done
        assert not stream.timed_out
        assert stream.lines_yielded == 3

    async def test_empty_stream(self):
        q: asyncio.Queue[str | None] = asyncio.Queue()
        await q.put(None)

        stream = LogStream(q)
        lines = [line async for line in stream]

        assert lines == []
        assert stream.done
        assert stream.lines_yielded == 0

    async def test_done_after_iteration(self):
        q: asyncio.Queue[str | None] = asyncio.Queue()
        await q.put("a")
        await q.put(None)

        stream = LogStream(q)
        async for _ in stream:
            pass

        assert stream.done

    async def test_iteration_after_done_yields_nothing(self):
        """Iterating a completed stream yields no more items."""
        q: asyncio.Queue[str | None] = asyncio.Queue()
        await q.put("a")
        await q.put(None)

        stream = LogStream(q)
        first_pass = [line async for line in stream]
        second_pass = [line async for line in stream]

        assert first_pass == ["a"]
        assert second_pass == []


# ---------------------------------------------------------------------------
# Item timeout
# ---------------------------------------------------------------------------


class TestLogStreamItemTimeout:
    async def test_item_timeout_stops_iteration(self):
        """If no item arrives within item_timeout, iteration ends."""
        q: asyncio.Queue[str | None] = asyncio.Queue()
        # Queue is empty — no items will arrive

        stream = LogStream(q, item_timeout=0.05)
        lines = [line async for line in stream]

        assert lines == []
        assert stream.done
        assert stream.timed_out

    async def test_item_timeout_after_some_lines(self):
        """Timeout can occur after some lines have been yielded."""
        q: asyncio.Queue[str | None] = asyncio.Queue()
        await q.put("line1")
        await q.put("line2")
        # No sentinel, no more items — will timeout

        stream = LogStream(q, item_timeout=0.05)
        lines = [line async for line in stream]

        assert lines == ["line1", "line2"]
        assert stream.done
        assert stream.timed_out
        assert stream.lines_yielded == 2

    async def test_item_timeout_resets_per_item(self):
        """Each item resets the per-item timeout clock."""
        q: asyncio.Queue[str | None] = asyncio.Queue()

        async def feed():
            for i in range(3):
                await asyncio.sleep(0.02)
                await q.put(f"line{i}")
            await asyncio.sleep(0.02)
            await q.put(None)

        stream = LogStream(q, item_timeout=0.1)

        # Start feeder concurrently
        feeder = asyncio.create_task(feed())
        lines = [line async for line in stream]
        await feeder

        assert lines == ["line0", "line1", "line2"]
        assert stream.done
        assert not stream.timed_out


# ---------------------------------------------------------------------------
# Total timeout
# ---------------------------------------------------------------------------


class TestLogStreamTotalTimeout:
    async def test_total_timeout_stops_iteration(self):
        """Stream ends after total_timeout regardless of item arrival."""
        q: asyncio.Queue[str | None] = asyncio.Queue()

        stream = LogStream(q, total_timeout=0.05)
        lines = [line async for line in stream]

        assert lines == []
        assert stream.done
        assert stream.timed_out

    async def test_total_timeout_with_slow_feed(self):
        """Total timeout cuts off a slow-but-steady feed."""
        q: asyncio.Queue[str | None] = asyncio.Queue()

        async def slow_feed():
            for i in range(100):
                await q.put(f"line{i}")
                await asyncio.sleep(0.02)
            await q.put(None)

        stream = LogStream(q, total_timeout=0.1)

        feeder = asyncio.create_task(slow_feed())
        lines = [line async for line in stream]
        feeder.cancel()
        try:
            await feeder
        except asyncio.CancelledError:
            pass

        # Should have gotten some lines but not all 100
        assert len(lines) > 0
        assert len(lines) < 100
        assert stream.done
        assert stream.timed_out

    async def test_total_timeout_with_item_timeout(self):
        """Both timeouts work together — total_timeout wins when it expires first."""
        q: asyncio.Queue[str | None] = asyncio.Queue()
        await q.put("line1")
        # No more items — item_timeout is long, but total_timeout is short

        stream = LogStream(q, item_timeout=10.0, total_timeout=0.05)
        lines = [line async for line in stream]

        assert lines == ["line1"]
        assert stream.done
        assert stream.timed_out


# ---------------------------------------------------------------------------
# Backpressure
# ---------------------------------------------------------------------------


class TestLogStreamBackpressure:
    async def test_handles_large_queue(self):
        """Stream handles a queue that fills up before iteration starts."""
        q: asyncio.Queue[str | None] = asyncio.Queue()
        for i in range(1000):
            await q.put(f"line{i}")
        await q.put(None)

        stream = LogStream(q)
        lines = [line async for line in stream]

        assert len(lines) == 1000
        assert stream.lines_yielded == 1000

    async def test_bounded_queue_consumer_keeps_up(self):
        """With a bounded queue, the stream consumes items, preventing producer blockage."""
        q: asyncio.Queue[str | None] = asyncio.Queue(maxsize=5)
        produced = 0

        async def producer():
            nonlocal produced
            for i in range(50):
                await q.put(f"line{i}")
                produced += 1
            await q.put(None)

        stream = LogStream(q)
        prod_task = asyncio.create_task(producer())
        lines = [line async for line in stream]
        await prod_task

        assert len(lines) == 50
        assert produced == 50
        assert stream.lines_yielded == 50


# ---------------------------------------------------------------------------
# Clean shutdown
# ---------------------------------------------------------------------------


class TestLogStreamShutdown:
    async def test_sentinel_triggers_clean_shutdown(self):
        """Sentinel (None) from LogCollector.finish() ends iteration cleanly."""
        collector = LogCollector()
        q = await collector.subscribe("m-1")

        await collector.push("m-1", "hello")
        await collector.push("m-1", "world")
        await collector.finish("m-1")

        stream = LogStream(q)
        lines = [line async for line in stream]

        assert lines == ["hello", "world"]
        assert stream.done
        assert not stream.timed_out

    async def test_concurrent_feed_and_finish(self):
        """Stream handles interleaved push and finish from another task."""
        collector = LogCollector()
        q = await collector.subscribe("m-2")

        async def feed_and_finish():
            for i in range(10):
                await collector.push("m-2", f"msg{i}")
                await asyncio.sleep(0.01)
            await collector.finish("m-2")

        stream = LogStream(q)
        feeder = asyncio.create_task(feed_and_finish())
        lines = [line async for line in stream]
        await feeder

        assert lines == [f"msg{i}" for i in range(10)]
        assert stream.done
        assert not stream.timed_out


# ---------------------------------------------------------------------------
# collect() convenience method
# ---------------------------------------------------------------------------


class TestLogStreamCollect:
    async def test_collect_returns_all_lines(self):
        q: asyncio.Queue[str | None] = asyncio.Queue()
        await q.put("a")
        await q.put("b")
        await q.put("c")
        await q.put(None)

        stream = LogStream(q)
        lines = await stream.collect()

        assert lines == ["a", "b", "c"]
        assert stream.done

    async def test_collect_with_timeout(self):
        q: asyncio.Queue[str | None] = asyncio.Queue()
        await q.put("x")
        # No sentinel — will timeout

        stream = LogStream(q, item_timeout=0.05)
        lines = await stream.collect()

        assert lines == ["x"]
        assert stream.timed_out

    async def test_collect_empty(self):
        q: asyncio.Queue[str | None] = asyncio.Queue()
        await q.put(None)

        stream = LogStream(q)
        lines = await stream.collect()

        assert lines == []
        assert stream.done


# ---------------------------------------------------------------------------
# __aiter__ protocol
# ---------------------------------------------------------------------------


class TestLogStreamProtocol:
    async def test_aiter_returns_self(self):
        q: asyncio.Queue[str | None] = asyncio.Queue()
        await q.put(None)
        stream = LogStream(q)
        assert stream.__aiter__() is stream

    async def test_manual_anext(self):
        q: asyncio.Queue[str | None] = asyncio.Queue()
        await q.put("first")
        await q.put("second")
        await q.put(None)

        stream = LogStream(q)
        assert await stream.__anext__() == "first"
        assert await stream.__anext__() == "second"
        with pytest.raises(StopAsyncIteration):
            await stream.__anext__()

    async def test_anext_after_done_raises_stop(self):
        q: asyncio.Queue[str | None] = asyncio.Queue()
        await q.put(None)

        stream = LogStream(q)
        with pytest.raises(StopAsyncIteration):
            await stream.__anext__()
        # Subsequent calls also raise
        with pytest.raises(StopAsyncIteration):
            await stream.__anext__()
