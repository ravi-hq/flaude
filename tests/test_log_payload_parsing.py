"""Tests for Sub-AC 6b: Parse and filter raw Fly log drain payloads.

Covers:
- Fly JSON envelope format variations (v1, v2, flat)
- Differentiating stdout from stderr from system/infrastructure messages
- System message filtering in LogDrainServer
- Edge cases in message extraction and stream classification
"""

from __future__ import annotations

import asyncio
import json

import pytest

from flaude.log_drain import (
    LogCollector,
    LogDrainServer,
    LogEntry,
    parse_log_entry,
    parse_ndjson,
)


# ---------------------------------------------------------------------------
# Fly log drain format samples
# ---------------------------------------------------------------------------
#
# Fly.io sends structured logs via HTTP POST as NDJSON. Multiple format
# variants exist across Fly generations. All samples below are representative
# of real payloads observed from Fly log drains.


def _fly_stdout(machine_id: str, message: str, **extra) -> dict:
    """Build a representative Fly log drain entry for user stdout."""
    return {
        "fly": {"app": {"instance": machine_id, "name": "myapp"}},
        "message": message,
        "stream": "stdout",
        "timestamp": "2024-01-01T00:00:00Z",
        **extra,
    }


def _fly_stderr(machine_id: str, message: str, **extra) -> dict:
    """Build a representative Fly log drain entry for user stderr."""
    return {
        "fly": {"app": {"instance": machine_id, "name": "myapp"}},
        "message": message,
        "stream": "stderr",
        "timestamp": "2024-01-01T00:00:00Z",
        **extra,
    }


def _fly_system(machine_id: str, message: str, source: str = "fly", **extra) -> dict:
    """Build a Fly infrastructure/system log entry (no stream field)."""
    return {
        "fly": {"app": {"instance": machine_id, "name": "myapp"}},
        "message": message,
        "source": source,  # "fly", "proxy", or "machine" — NOT "app"
        "timestamp": "2024-01-01T00:00:00Z",
        **extra,
    }


# ---------------------------------------------------------------------------
# parse_log_entry — stream classification
# ---------------------------------------------------------------------------


class TestStreamClassification:
    """Verify the three-way stdout/stderr/system classification."""

    def test_explicit_stdout_stream(self):
        """An entry with stream=stdout is classified as stdout."""
        entry = parse_log_entry(_fly_stdout("m-1", "hello from user"))
        assert entry is not None
        assert entry.stream == "stdout"

    def test_explicit_stderr_stream(self):
        """An entry with stream=stderr is classified as stderr."""
        entry = parse_log_entry(_fly_stderr("m-1", "error from user"))
        assert entry is not None
        assert entry.stream == "stderr"

    def test_fly_source_classified_as_system(self):
        """Fly infrastructure messages (source=fly) become stream=system."""
        entry = parse_log_entry(_fly_system("m-1", "machine is starting", source="fly"))
        assert entry is not None
        assert entry.stream == "system"

    def test_proxy_source_classified_as_system(self):
        """Fly proxy logs (source=proxy) become stream=system."""
        entry = parse_log_entry(_fly_system("m-1", "proxy connected", source="proxy"))
        assert entry is not None
        assert entry.stream == "system"

    def test_machine_source_classified_as_system(self):
        """Machine-level lifecycle logs (source=machine) become stream=system."""
        entry = parse_log_entry(_fly_system("m-1", "stopping machine", source="machine"))
        assert entry is not None
        assert entry.stream == "system"

    def test_no_stream_no_source_defaults_to_stdout(self):
        """Entries with neither stream nor source default to stdout.

        This preserves backward-compatibility for apps that emit JSON logs
        without explicit stream tagging.
        """
        raw = {"fly": {"app": {"instance": "m-1"}}, "message": "plain log"}
        entry = parse_log_entry(raw)
        assert entry is not None
        assert entry.stream == "stdout"

    def test_app_source_no_stream_defaults_to_stdout(self):
        """source=app with no stream field is treated as user stdout."""
        raw = {
            "fly": {"app": {"instance": "m-1"}},
            "message": "user log",
            "source": "app",
        }
        entry = parse_log_entry(raw)
        assert entry is not None
        assert entry.stream == "stdout"

    def test_explicit_stream_takes_precedence_over_source(self):
        """When both stream and source are present, stream wins."""
        raw = {
            "fly": {"app": {"instance": "m-1"}},
            "message": "conflicting fields",
            "stream": "stdout",
            "source": "fly",  # system source, but explicit stream wins
        }
        entry = parse_log_entry(raw)
        assert entry is not None
        assert entry.stream == "stdout"

    def test_stderr_with_fly_source_uses_explicit_stream(self):
        """Even with source=fly, an explicit stream=stderr wins."""
        raw = {
            "fly": {"app": {"instance": "m-1"}},
            "message": "stderr with system source",
            "stream": "stderr",
            "source": "fly",
        }
        entry = parse_log_entry(raw)
        assert entry is not None
        assert entry.stream == "stderr"


# ---------------------------------------------------------------------------
# parse_log_entry — Fly JSON envelope format variants
# ---------------------------------------------------------------------------


class TestFlyEnvelopeFormats:
    """Verify machine ID extraction across Fly log drain format variants."""

    def test_v1_fly_app_instance(self):
        """Fly v1: machine ID in fly.app.instance."""
        raw = {
            "fly": {"app": {"instance": "m-abc123", "name": "myapp"}},
            "message": "line from v1",
            "stream": "stdout",
        }
        entry = parse_log_entry(raw)
        assert entry is not None
        assert entry.machine_id == "m-abc123"
        assert entry.app_name == "myapp"

    def test_v2_fly_machine_id(self):
        """Fly v2: machine ID in fly.machine.id."""
        raw = {
            "fly": {"machine": {"id": "m-xyz789"}, "app": {"name": "myapp"}},
            "message": "line from v2",
            "stream": "stdout",
        }
        entry = parse_log_entry(raw)
        assert entry is not None
        assert entry.machine_id == "m-xyz789"

    def test_flat_instance_field(self):
        """Legacy flat format with top-level 'instance' field."""
        raw = {"instance": "m-flat", "message": "from flat format", "stream": "stdout"}
        entry = parse_log_entry(raw)
        assert entry is not None
        assert entry.machine_id == "m-flat"

    def test_flat_machine_id_field(self):
        """Legacy flat format with top-level 'machine_id' field."""
        raw = {"machine_id": "m-direct", "message": "direct id", "stream": "stdout"}
        entry = parse_log_entry(raw)
        assert entry is not None
        assert entry.machine_id == "m-direct"

    def test_message_field_alias_log(self):
        """'log' field used instead of 'message' (vector/logfmt format)."""
        raw = {
            "fly": {"app": {"instance": "m-1"}},
            "log": "via log field",
            "stream": "stdout",
        }
        entry = parse_log_entry(raw)
        assert entry is not None
        assert entry.message == "via log field"

    def test_message_field_alias_msg(self):
        """'msg' field used instead of 'message'."""
        raw = {
            "fly": {"app": {"instance": "m-1"}},
            "msg": "via msg field",
            "stream": "stdout",
        }
        entry = parse_log_entry(raw)
        assert entry is not None
        assert entry.message == "via msg field"

    def test_timestamp_variants(self):
        """Various timestamp field names are normalised."""
        for ts_key in ("timestamp", "time", "ts"):
            raw = {
                "fly": {"app": {"instance": "m-1"}},
                "message": "timestamped",
                "stream": "stdout",
                ts_key: "2024-06-15T12:00:00Z",
            }
            entry = parse_log_entry(raw)
            assert entry is not None, f"Failed for ts_key={ts_key}"
            assert entry.timestamp == "2024-06-15T12:00:00Z", f"Failed for ts_key={ts_key}"

    def test_non_string_message_coerced(self):
        """Non-string message values are coerced to str."""
        raw = {"fly": {"app": {"instance": "m-1"}}, "message": 42, "stream": "stdout"}
        entry = parse_log_entry(raw)
        assert entry is not None
        assert entry.message == "42"

    def test_empty_message_allowed(self):
        """Empty message strings are preserved (blank lines are valid output)."""
        raw = {"fly": {"app": {"instance": "m-1"}}, "message": "", "stream": "stdout"}
        entry = parse_log_entry(raw)
        assert entry is not None
        assert entry.message == ""

    def test_raw_dict_preserved(self):
        """The full raw dict is preserved on the LogEntry for introspection."""
        raw = {
            "fly": {"app": {"instance": "m-1", "name": "app"}},
            "message": "test",
            "stream": "stdout",
            "custom_field": "preserved",
        }
        entry = parse_log_entry(raw)
        assert entry is not None
        assert entry.raw is raw  # same object, not a copy


# ---------------------------------------------------------------------------
# LogDrainServer — system message filtering
# ---------------------------------------------------------------------------


class TestSystemMessageFiltering:
    """Verify the server filters system messages and keeps only user output."""

    async def test_system_messages_always_filtered(self):
        """System stream entries (source=fly) never reach the queue."""
        collector = LogCollector()
        q = await collector.subscribe("m-1")
        server = LogDrainServer(collector, port=0)
        await server.start()

        try:
            entries = [
                _fly_system("m-1", "machine is starting", source="fly"),
                _fly_stdout("m-1", "first user line"),
                _fly_system("m-1", "health check ok", source="proxy"),
                _fly_stdout("m-1", "second user line"),
                _fly_system("m-1", "machine stopped", source="machine"),
            ]
            body = "\n".join(json.dumps(e) for e in entries).encode()
            await _http_post(server.actual_port, body)
            await asyncio.sleep(0.1)

            # Only the two user stdout lines should arrive
            assert q.get_nowait() == "first user line"
            assert q.get_nowait() == "second user line"
            assert q.qsize() == 0
        finally:
            await server.stop()

    async def test_system_messages_filtered_even_with_include_stderr(self):
        """System messages are filtered regardless of include_stderr setting."""
        collector = LogCollector()
        q = await collector.subscribe("m-1")
        server = LogDrainServer(collector, port=0, include_stderr=True)
        await server.start()

        try:
            entries = [
                _fly_stdout("m-1", "stdout line"),
                _fly_stderr("m-1", "stderr line"),
                _fly_system("m-1", "infrastructure event", source="fly"),
            ]
            body = "\n".join(json.dumps(e) for e in entries).encode()
            await _http_post(server.actual_port, body)
            await asyncio.sleep(0.1)

            assert q.get_nowait() == "stdout line"
            assert q.get_nowait() == "stderr line"
            assert q.qsize() == 0  # system message was filtered
        finally:
            await server.stop()

    async def test_proxy_source_filtered(self):
        """Proxy log entries (source=proxy) are classified as system and dropped."""
        collector = LogCollector()
        q = await collector.subscribe("m-1")
        server = LogDrainServer(collector, port=0)
        await server.start()

        try:
            entries = [
                _fly_system("m-1", "proxy connected", source="proxy"),
                _fly_stdout("m-1", "actual output"),
            ]
            body = "\n".join(json.dumps(e) for e in entries).encode()
            await _http_post(server.actual_port, body)
            await asyncio.sleep(0.1)

            assert q.get_nowait() == "actual output"
            assert q.qsize() == 0
        finally:
            await server.stop()

    async def test_machine_source_filtered(self):
        """Machine lifecycle entries (source=machine) are dropped."""
        collector = LogCollector()
        q = await collector.subscribe("m-1")
        server = LogDrainServer(collector, port=0)
        await server.start()

        try:
            entries = [
                _fly_system("m-1", "machine stopping", source="machine"),
                _fly_stdout("m-1", "final output"),
            ]
            body = "\n".join(json.dumps(e) for e in entries).encode()
            await _http_post(server.actual_port, body)
            await asyncio.sleep(0.1)

            assert q.get_nowait() == "final output"
            assert q.qsize() == 0
        finally:
            await server.stop()

    async def test_no_stream_no_source_passes_through(self):
        """Entries with neither stream nor source field default to stdout and pass through."""
        collector = LogCollector()
        q = await collector.subscribe("m-1")
        server = LogDrainServer(collector, port=0)
        await server.start()

        try:
            entries = [
                # No stream, no source — treated as user stdout
                {"fly": {"app": {"instance": "m-1"}}, "message": "plain log"},
            ]
            body = "\n".join(json.dumps(e) for e in entries).encode()
            await _http_post(server.actual_port, body)
            await asyncio.sleep(0.1)

            assert q.get_nowait() == "plain log"
        finally:
            await server.stop()

    async def test_mixed_batch_ordering_preserved(self):
        """When stdout and system entries are mixed, order of stdout is preserved."""
        collector = LogCollector()
        q = await collector.subscribe("m-1")
        server = LogDrainServer(collector, port=0)
        await server.start()

        try:
            entries = [
                _fly_stdout("m-1", "line-1"),
                _fly_system("m-1", "sys-a", source="fly"),
                _fly_stdout("m-1", "line-2"),
                _fly_system("m-1", "sys-b", source="proxy"),
                _fly_stdout("m-1", "line-3"),
            ]
            body = "\n".join(json.dumps(e) for e in entries).encode()
            await _http_post(server.actual_port, body)
            await asyncio.sleep(0.1)

            lines = []
            while not q.empty():
                lines.append(q.get_nowait())
            assert lines == ["line-1", "line-2", "line-3"]
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# parse_ndjson — edge cases
# ---------------------------------------------------------------------------


class TestParseNdjsonEdgeCases:
    def test_windows_line_endings(self):
        """CRLF line endings are handled correctly."""
        body = b'{"a":1}\r\n{"b":2}\r\n'
        result = parse_ndjson(body)
        assert len(result) == 2

    def test_trailing_whitespace_stripped(self):
        """Trailing whitespace on each line is stripped before parsing."""
        body = b'{"a":1}   \n{"b":2}  '
        result = parse_ndjson(body)
        assert len(result) == 2

    def test_deeply_nested_json_valid(self):
        """Deeply nested JSON objects are parsed without issue."""
        deep = {"a": {"b": {"c": {"d": "value"}}}}
        body = json.dumps(deep).encode()
        result = parse_ndjson(body)
        assert len(result) == 1
        assert result[0]["a"]["b"]["c"]["d"] == "value"

    def test_unicode_in_messages(self):
        """Unicode characters (emojis, non-ASCII) are preserved."""
        raw = {"instance": "m-1", "message": "résumé 🚀 日本語", "stream": "stdout"}
        body = json.dumps(raw, ensure_ascii=False).encode("utf-8")
        entries = parse_ndjson(body)
        assert len(entries) == 1
        entry = parse_log_entry(entries[0])
        assert entry is not None
        assert entry.message == "résumé 🚀 日本語"


# ---------------------------------------------------------------------------
# Integration: full payload round-trip with system message filtering
# ---------------------------------------------------------------------------


class TestFullPayloadRoundTrip:
    """End-to-end tests simulating a real Fly log drain POST payload."""

    async def test_realistic_mixed_payload(self):
        """A realistic Fly payload containing stdout, stderr, and system entries.

        Simulates what Fly actually sends: machine lifecycle messages interleaved
        with user process output.
        """
        collector = LogCollector()
        q = await collector.subscribe("m-run-42")
        server = LogDrainServer(collector, port=0, include_stderr=False)
        await server.start()

        try:
            # A realistic payload with all three stream types interleaved
            payload_lines = [
                # Fly starts the machine
                json.dumps(_fly_system("m-run-42", "[machine] starting", source="fly")),
                # User process starts and emits stdout
                json.dumps(_fly_stdout("m-run-42", "Cloning repo...")),
                json.dumps(_fly_stdout("m-run-42", "Running claude...")),
                # User process emits stderr (filtered when include_stderr=False)
                json.dumps(_fly_stderr("m-run-42", "Warning: deprecated flag")),
                # User process continues
                json.dumps(_fly_stdout("m-run-42", "Claude Code output line 1")),
                json.dumps(_fly_stdout("m-run-42", "Claude Code output line 2")),
                # Fly proxy logs
                json.dumps(_fly_system("m-run-42", "connection established", source="proxy")),
                # Final user output
                json.dumps(_fly_stdout("m-run-42", "[flaude:exit:0]")),
                # Fly stops the machine
                json.dumps(_fly_system("m-run-42", "[machine] stopped", source="machine")),
            ]
            body = "\n".join(payload_lines).encode()
            await _http_post(server.actual_port, body)
            await asyncio.sleep(0.1)

            # Only stdout lines should arrive, in order
            lines = []
            while not q.empty():
                lines.append(q.get_nowait())

            assert lines == [
                "Cloning repo...",
                "Running claude...",
                "Claude Code output line 1",
                "Claude Code output line 2",
                "[flaude:exit:0]",
            ]
        finally:
            await server.stop()

    async def test_realistic_mixed_payload_with_stderr(self):
        """Same as above but include_stderr=True also captures user stderr."""
        collector = LogCollector()
        q = await collector.subscribe("m-run-43")
        server = LogDrainServer(collector, port=0, include_stderr=True)
        await server.start()

        try:
            payload_lines = [
                json.dumps(_fly_system("m-run-43", "[machine] starting", source="fly")),
                json.dumps(_fly_stdout("m-run-43", "stdout line")),
                json.dumps(_fly_stderr("m-run-43", "stderr line")),
                json.dumps(_fly_system("m-run-43", "[machine] stopped", source="machine")),
            ]
            body = "\n".join(payload_lines).encode()
            await _http_post(server.actual_port, body)
            await asyncio.sleep(0.1)

            lines = []
            while not q.empty():
                lines.append(q.get_nowait())

            assert lines == ["stdout line", "stderr line"]
        finally:
            await server.stop()

    async def test_multiple_machines_in_one_payload(self):
        """A single POST may contain log entries for multiple machines."""
        collector = LogCollector()
        qa = await collector.subscribe("m-a")
        qb = await collector.subscribe("m-b")
        server = LogDrainServer(collector, port=0)
        await server.start()

        try:
            payload_lines = [
                json.dumps(_fly_system("m-a", "machine starting", source="fly")),
                json.dumps(_fly_stdout("m-a", "a-line-1")),
                json.dumps(_fly_system("m-b", "machine starting", source="fly")),
                json.dumps(_fly_stdout("m-b", "b-line-1")),
                json.dumps(_fly_stdout("m-a", "a-line-2")),
                json.dumps(_fly_system("m-a", "machine stopped", source="machine")),
                json.dumps(_fly_stdout("m-b", "b-line-2")),
            ]
            body = "\n".join(payload_lines).encode()
            await _http_post(server.actual_port, body)
            await asyncio.sleep(0.1)

            a_lines = []
            while not qa.empty():
                a_lines.append(qa.get_nowait())

            b_lines = []
            while not qb.empty():
                b_lines.append(qb.get_nowait())

            assert a_lines == ["a-line-1", "a-line-2"]
            assert b_lines == ["b-line-1", "b-line-2"]
        finally:
            await server.stop()

    async def test_all_system_payload_delivers_nothing(self):
        """A payload consisting entirely of system messages delivers no lines."""
        collector = LogCollector()
        q = await collector.subscribe("m-1")
        server = LogDrainServer(collector, port=0)
        await server.start()

        try:
            payload_lines = [
                json.dumps(_fly_system("m-1", "starting", source="fly")),
                json.dumps(_fly_system("m-1", "health ok", source="proxy")),
                json.dumps(_fly_system("m-1", "stopped", source="machine")),
            ]
            body = "\n".join(payload_lines).encode()
            await _http_post(server.actual_port, body)
            await asyncio.sleep(0.1)

            assert q.qsize() == 0
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    await asyncio.wait_for(reader.read(1024), timeout=5.0)
    writer.close()
    await writer.wait_closed()
