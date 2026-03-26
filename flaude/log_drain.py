"""HTTP log drain receiver for capturing Fly.io machine stdout logs.

Fly.io log drains deliver logs as NDJSON via HTTP POST. This module provides:

- ``LogCollector``: Registry that maps machine IDs → asyncio.Queue instances.
- ``LogDrainServer``: Lightweight HTTP server (stdlib asyncio) that receives
  Fly log drain POSTs, parses log entries, and routes stdout lines to the
  correct queue.

Typical usage::

    collector = LogCollector()
    server = LogDrainServer(collector, port=9999)
    await server.start()

    q = collector.subscribe("machine-id-abc")
    async for line in drain_queue(q):
        print(line)

    await server.stop()
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Sentinel value pushed to a queue to signal that the machine has finished
# and no more log lines will arrive.
_SENTINEL = None


@dataclass
class LogEntry:
    """Parsed representation of a single Fly.io log drain entry.

    Attributes:
        machine_id: The Fly machine ID that produced this log entry.
        message: The log line content.
        stream: Output stream — ``"stdout"``, ``"stderr"``, or ``"system"``
            (Fly infrastructure messages).
        timestamp: ISO 8601 timestamp string from the log drain payload (may be empty).
        app_name: The Fly.io application name (may be empty if not present in payload).
        raw: The original parsed JSON dict from the log drain request.
    """

    machine_id: str
    message: str
    stream: str  # "stdout" or "stderr"
    timestamp: str = ""
    app_name: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class LogCollector:
    """Thread-safe registry mapping machine IDs to asyncio queues.

    Each machine gets its own queue. Log entries are routed by machine ID.
    """

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[str | None]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, machine_id: str) -> asyncio.Queue[str | None]:
        """Get or create a queue for the given machine ID.

        The queue yields ``str`` log lines. A ``None`` sentinel indicates
        that the machine has stopped and no more logs will arrive.
        """
        async with self._lock:
            if machine_id not in self._queues:
                self._queues[machine_id] = asyncio.Queue()
            return self._queues[machine_id]

    async def push(self, machine_id: str, line: str) -> None:
        """Push a log line to the queue for *machine_id*.

        If no subscriber exists for this machine, the line is silently dropped.

        Args:
            machine_id: The Fly machine ID whose queue should receive the line.
            line: The log line string to enqueue.
        """
        async with self._lock:
            q = self._queues.get(machine_id)
        if q is not None:
            await q.put(line)

    async def finish(self, machine_id: str) -> None:
        """Signal that no more logs will arrive for *machine_id*.

        Pushes a ``None`` sentinel and removes the queue from the registry.

        Args:
            machine_id: The Fly machine ID whose queue should be finalized.
        """
        async with self._lock:
            q = self._queues.pop(machine_id, None)
        if q is not None:
            await q.put(_SENTINEL)

    async def finish_all(self) -> None:
        """Signal completion for all registered machines."""
        async with self._lock:
            machine_ids = list(self._queues.keys())
        for mid in machine_ids:
            await self.finish(mid)

    @property
    def machine_ids(self) -> list[str]:
        """Return list of currently subscribed machine IDs."""
        return list(self._queues.keys())


def parse_log_entry(raw: dict[str, Any]) -> LogEntry | None:
    """Parse a single Fly.io log drain JSON object into a LogEntry.

    Fly log drain NDJSON format varies, but typically contains:
    - ``fly.app.instance`` or ``fly.machine.id``: machine identifier
    - ``message``, ``log``, or ``msg``: the log line content
    - ``stream``: ``"stdout"`` or ``"stderr"`` for user process output
    - ``source``: log origin — ``"app"`` for user code, ``"fly"``/``"proxy"``/
      ``"machine"`` for Fly infrastructure messages

    Stream determination logic:

    1. If the ``stream`` field is explicitly set, use it as-is.
    2. If no ``stream`` field but ``source`` indicates Fly infrastructure
       (``"fly"``, ``"proxy"``, or ``"machine"``), the entry is classified
       as ``"system"`` — a Fly-generated lifecycle or health message that
       should not appear in user-facing log output.
    3. If neither field is set, default to ``"stdout"`` (user application
       log without explicit stream tagging).

    Returns None if the entry cannot be parsed or is missing required fields.
    """
    # Extract machine ID — Fly uses different field names across versions
    machine_id = (
        raw.get("fly", {}).get("app", {}).get("instance")
        or raw.get("fly", {}).get("machine", {}).get("id")
        or raw.get("instance")
        or raw.get("machine_id")
        or ""
    )
    if not machine_id:
        return None

    # Extract message — accept multiple field name aliases
    message = raw.get("message") or raw.get("log") or raw.get("msg") or ""
    if not isinstance(message, str):
        message = str(message)

    # Determine stream type, carefully separating user output from system logs.
    #
    # Fly's "source" field indicates log *origin* (who produced the log), not
    # the output stream. Sources "fly", "proxy", and "machine" are Fly
    # infrastructure; "app" (or absent) means user process output.
    explicit_stream = raw.get("stream") or ""
    if explicit_stream:
        # Explicit stream tag — trust it directly ("stdout", "stderr", etc.)
        stream = explicit_stream
    else:
        source = raw.get("source") or ""
        if source in ("fly", "proxy", "machine"):
            # Fly infrastructure message (lifecycle events, health checks, etc.)
            stream = "system"
        else:
            # No stream indicator and no system source — treat as user stdout.
            # This covers apps that write to stdout without stream metadata.
            stream = "stdout"

    # Extract optional metadata
    timestamp = raw.get("timestamp") or raw.get("time") or raw.get("ts") or ""
    app_name = raw.get("fly", {}).get("app", {}).get("name") or ""

    return LogEntry(
        machine_id=machine_id,
        message=message,
        stream=stream,
        timestamp=str(timestamp),
        app_name=app_name,
        raw=raw,
    )


def parse_ndjson(body: bytes) -> list[dict[str, Any]]:
    """Parse an NDJSON (newline-delimited JSON) body into a list of dicts.

    Silently skips malformed lines.

    Args:
        body: Raw HTTP request body containing newline-delimited JSON.

    Returns:
        List of successfully parsed JSON objects. Malformed lines are omitted.
    """
    entries: list[dict[str, Any]] = []
    for line in body.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                entries.append(obj)
        except (json.JSONDecodeError, ValueError):
            logger.debug("Skipping malformed NDJSON line: %s", line[:200])
    return entries


class LogDrainServer:
    """Minimal HTTP server that receives Fly.io log drain POSTs.

    Fly.io sends logs as NDJSON via HTTP POST to a configured endpoint.
    This server:

    1. Listens on the specified host/port
    2. Accepts POST requests on any path
    3. Parses NDJSON bodies into log entries
    4. Filters for stdout from subscribed machines
    5. Pushes matching lines into the LogCollector queues

    The server is implemented using stdlib ``asyncio`` to avoid extra
    dependencies. It handles HTTP/1.1 at a minimal level sufficient for
    Fly log drains.
    """

    def __init__(
        self,
        collector: LogCollector,
        *,
        host: str = "0.0.0.0",
        port: int = 0,
        include_stderr: bool = False,
    ) -> None:
        """Initialize the log drain server.

        Args:
            collector: The :class:`LogCollector` that receives and routes log entries.
            host: Network interface to bind to. Defaults to ``0.0.0.0`` (all interfaces).
            port: TCP port to listen on. Use ``0`` for OS-assigned ephemeral port.
            include_stderr: If True, stderr lines are forwarded to collectors in
                addition to stdout. Defaults to False.
        """
        self.collector = collector
        self.host = host
        self.port = port
        self.include_stderr = include_stderr
        self._server: asyncio.Server | None = None
        self._actual_port: int | None = None

    @property
    def actual_port(self) -> int | None:
        """The port the server is actually listening on (after start)."""
        return self._actual_port

    @property
    def url(self) -> str | None:
        """The base URL of the running server, or None if not started."""
        if self._actual_port is None:
            return None
        host = self.host if self.host != "0.0.0.0" else "127.0.0.1"
        return f"http://{host}:{self._actual_port}"

    async def start(self) -> None:
        """Start the HTTP server."""
        self._server = await asyncio.start_server(
            self._handle_connection, self.host, self.port
        )
        # Resolve the actual port (useful when port=0 for auto-assignment)
        sockets = self._server.sockets
        if sockets:
            self._actual_port = sockets[0].getsockname()[1]
        logger.info("Log drain server listening on %s:%s", self.host, self._actual_port)

    async def stop(self) -> None:
        """Stop the HTTP server and signal all collectors."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            self._actual_port = None
        logger.info("Log drain server stopped")

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a single HTTP connection from Fly log drain."""
        try:
            await self._process_request(reader, writer)
        except Exception:
            logger.debug("Error handling log drain connection", exc_info=True)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _process_request(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Parse HTTP request and route log entries."""
        # Read request line
        request_line = await asyncio.wait_for(reader.readline(), timeout=10.0)
        if not request_line:
            return

        parts = request_line.decode("utf-8", errors="replace").strip().split(" ")
        method = parts[0] if parts else ""

        # Read headers
        content_length = 0
        while True:
            header_line = await asyncio.wait_for(reader.readline(), timeout=10.0)
            if header_line in (b"\r\n", b"\n", b""):
                break
            header = header_line.decode("utf-8", errors="replace").strip().lower()
            if header.startswith("content-length:"):
                try:
                    content_length = int(header.split(":", 1)[1].strip())
                except ValueError:
                    pass

        # Read body
        body = b""
        if content_length > 0:
            body = await asyncio.wait_for(
                reader.readexactly(content_length), timeout=30.0
            )

        # Process POST requests
        if method == "POST" and body:
            entries = parse_ndjson(body)
            await self._route_entries(entries)

        # Send 200 OK response
        response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Length: 0\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )
        writer.write(response)
        await writer.drain()

    async def _route_entries(self, entries: list[dict[str, Any]]) -> None:
        """Parse and route log entries to appropriate queues.

        Only user-process output is forwarded:
        - ``stdout`` is always included.
        - ``stderr`` is included when *include_stderr* is True.
        - ``system`` (Fly infrastructure messages) is **always** filtered out;
          these are lifecycle events produced by Fly, not the user's process.
        """
        for raw in entries:
            entry = parse_log_entry(raw)
            if entry is None:
                continue

            # System-stream entries are Fly infrastructure messages — skip them.
            if entry.stream == "system":
                logger.debug(
                    "Filtered system log from machine %s: %s",
                    entry.machine_id,
                    entry.message[:80],
                )
                continue

            # Forward stdout; forward stderr only when explicitly requested.
            if entry.stream == "stdout" or (self.include_stderr and entry.stream == "stderr"):
                await self.collector.push(entry.machine_id, entry.message)


async def drain_queue(
    queue: asyncio.Queue[str | None],
    *,
    timeout: float | None = None,
) -> list[str]:
    """Drain all lines from a queue until the sentinel is received.

    This is a convenience function for collecting all output. For streaming,
    use ``async_iter_queue`` instead.

    Args:
        queue: The queue to drain (from ``LogCollector.subscribe``).
        timeout: Max seconds to wait for all output. None = wait forever.

    Returns:
        List of log lines (excluding the sentinel).
    """
    lines: list[str] = []

    async def _drain() -> list[str]:
        while True:
            item = await queue.get()
            if item is None:
                return lines
            lines.append(item)

    if timeout is not None:
        return await asyncio.wait_for(_drain(), timeout=timeout)
    return await _drain()


async def async_iter_queue(
    queue: asyncio.Queue[str | None],
) -> Any:  # AsyncIterator[str] — using Any to avoid import issues
    """Async iterator that yields log lines from a queue until sentinel.

    Usage::

        q = await collector.subscribe("machine-id")
        async for line in async_iter_queue(q):
            print(line)
    """
    while True:
        item = await queue.get()
        if item is None:
            return
        yield item


class LogStream:
    """Async iterator wrapper that yields parsed log lines from a queue.

    Provides backpressure handling, per-item and overall timeouts, and clean
    shutdown when the machine completes (sentinel received) or on cancellation.

    Usage::

        stream = LogStream(queue, item_timeout=30.0, total_timeout=3600.0)
        async for line in stream:
            print(line)

        # Check final state
        assert stream.done
        print(f"Received {stream.lines_yielded} lines")

    Args:
        queue: The asyncio.Queue to read from (from ``LogCollector.subscribe``).
            A ``None`` sentinel signals end-of-stream.
        item_timeout: Max seconds to wait for each individual log line.
            ``None`` means wait indefinitely for each item.
        total_timeout: Max seconds for the entire iteration.
            ``None`` means no overall time limit.
    """

    def __init__(
        self,
        queue: asyncio.Queue[str | None],
        *,
        item_timeout: float | None = None,
        total_timeout: float | None = None,
    ) -> None:
        self._queue = queue
        self._item_timeout = item_timeout
        self._total_timeout = total_timeout

        # Mutable state
        self._done = False
        self._timed_out = False
        self._lines_yielded = 0
        self._deadline: float | None = None
        self._started = False

    @property
    def done(self) -> bool:
        """True if the stream has finished (sentinel received or timed out)."""
        return self._done

    @property
    def timed_out(self) -> bool:
        """True if the stream ended due to a timeout."""
        return self._timed_out

    @property
    def lines_yielded(self) -> int:
        """Number of log lines yielded so far."""
        return self._lines_yielded

    def __aiter__(self) -> LogStream:
        return self

    async def __anext__(self) -> str:
        if self._done:
            raise StopAsyncIteration

        # Set the deadline on the first call
        if not self._started:
            self._started = True
            if self._total_timeout is not None:
                loop = asyncio.get_event_loop()
                self._deadline = loop.time() + self._total_timeout

        # Calculate effective timeout for this get()
        effective_timeout = self._item_timeout
        if self._deadline is not None:
            loop = asyncio.get_event_loop()
            remaining = self._deadline - loop.time()
            if remaining <= 0:
                self._done = True
                self._timed_out = True
                raise StopAsyncIteration
            if effective_timeout is None:
                effective_timeout = remaining
            else:
                effective_timeout = min(effective_timeout, remaining)

        try:
            if effective_timeout is not None:
                item = await asyncio.wait_for(
                    self._queue.get(), timeout=effective_timeout
                )
            else:
                item = await self._queue.get()
        except asyncio.TimeoutError:
            self._done = True
            self._timed_out = True
            raise StopAsyncIteration

        # Sentinel means the machine is done
        if item is None:
            self._done = True
            raise StopAsyncIteration

        self._lines_yielded += 1
        return item

    async def collect(self) -> list[str]:
        """Consume all remaining lines and return them as a list.

        Convenience method equivalent to ``[line async for line in self]``.
        """
        lines: list[str] = []
        async for line in self:
            lines.append(line)
        return lines
