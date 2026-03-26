# Log Drain Infrastructure

flaude's real-time log streaming relies on Fly.io's log drain mechanism combined with a
lightweight local HTTP server. This page explains how the pieces fit together.

## How Fly.io delivers logs

Fly.io machines emit logs via stdout and stderr. When a log drain is configured on an app,
Fly delivers those logs to an HTTP endpoint as NDJSON (newline-delimited JSON):

```
POST http://your-drain-url/
Content-Type: application/x-ndjson

{"fly":{"app":{"instance":"abc123"}},"message":"Starting Claude Code...","stream":"stdout"}
{"fly":{"app":{"instance":"abc123"}},"message":"Reading files...","stream":"stdout"}
```

Each line is a JSON object. Multiple log lines may arrive in a single POST body. Fly sends
batches every few hundred milliseconds.

## LogDrainServer

`LogDrainServer` is a minimal HTTP server built with stdlib `asyncio.start_server` — no
web framework, no ASGI, no extra dependencies.

```
LogDrainServer
├── asyncio.start_server (TCP server)
├── _handle_connection (one coroutine per connection)
│   └── _process_request
│       ├── Read HTTP request line + headers
│       ├── Read body (Content-Length bytes)
│       ├── parse_ndjson(body) → list of dicts
│       └── _route_entries → LogCollector.push(machine_id, message)
└── 200 OK response (no body)
```

The server binds to `0.0.0.0:0` by default — port 0 tells the OS to assign an available
ephemeral port. After `server.start()`, the actual port is available as `server.actual_port`.

## Stream classification

Not all log entries that Fly delivers are user application output. flaude classifies each
entry into one of three streams:

| Stream | Source | Handling |
|--------|--------|----------|
| `stdout` | User process stdout | Forwarded to `LogCollector` |
| `stderr` | User process stderr | Forwarded only if `include_stderr=True` |
| `system` | Fly infrastructure (lifecycle events, health checks) | Always filtered out |

The classification logic:

1. If the entry has an explicit `stream` field, use it directly.
2. Otherwise, check the `source` field — values `"fly"`, `"proxy"`, or `"machine"`
   indicate Fly infrastructure and are classified as `"system"`.
3. If neither field is present, default to `"stdout"` (user code without stream metadata).

System log entries — machine boot events, health check results, proxy messages — are
silently discarded so they never appear in your application's log stream.

## LogCollector

`LogCollector` is a registry that maps machine IDs to `asyncio.Queue` instances:

```
LogCollector
└── _queues: dict[machine_id → asyncio.Queue[str | None]]

subscribe(machine_id) → asyncio.Queue   # create queue for this machine
push(machine_id, line)                  # route line to correct queue
finish(machine_id)                      # push None sentinel, remove queue
finish_all()                            # finish all registered machines
```

When `LogDrainServer` routes a log entry, it calls `collector.push(machine_id, message)`.
If no queue is registered for that machine (e.g. subscription hasn't been set up yet), the
line is silently dropped. This is why `run_with_logs` subscribes to the collector
**before** creating the machine — to avoid losing early log lines.

## The sentinel pattern

The end of a machine's log stream is signalled by pushing `None` (the sentinel) into the
queue:

```
Queue contents during normal operation:
  "Cloning repo..."
  "Running Claude Code..."
  "Found 3 issues."
  None              ← sentinel: stream is done
```

`LogStream` watches for `None` and raises `StopAsyncIteration` when it sees it, which
cleanly ends the `async for` loop.

`LogCollector.finish(machine_id)` pushes the sentinel and removes the queue from the
registry. It is called from the `_wait_signal_destroy` background task in `lifecycle.py`
after the machine exits — regardless of success or failure, via a `finally` block.

## LogStream

`LogStream` is the async iterator wrapper that your code consumes:

```python
stream = LogStream(queue, item_timeout=30.0, total_timeout=3600.0)
async for line in stream:
    print(line)
```

Internally it calls `asyncio.wait_for(queue.get(), timeout=effective_timeout)` for each
item. The effective timeout is the minimum of `item_timeout` and the remaining
`total_timeout` budget:

```
effective_timeout = min(item_timeout, deadline - now)
```

When a timeout fires, `LogStream` sets `stream.timed_out = True`, marks itself as done,
and raises `StopAsyncIteration` — ending the `async for` loop without raising an exception
to the caller. Use `asyncio.TimeoutError` (from `wait_timeout` on `run_and_destroy` /
`run_with_logs`) for hard wall-clock limits on the entire execution.

## End-to-end flow

```
Machine stdout
    │
    ▼ Fly.io log drain
LogDrainServer (HTTP POST, NDJSON)
    │ parse_ndjson + parse_log_entry
    │ filter: stream != "system"
    ▼
LogCollector.push(machine_id, message)
    │
    ▼ asyncio.Queue[str | None]
LogStream.__anext__()
    │ asyncio.wait_for(queue.get(), timeout)
    ▼
async for line in stream:
    print(line)          ← your code
```

When the machine exits:

```
_wait_signal_destroy (background task)
    │
    ├─► LogCollector.finish(machine_id)  → None sentinel → queue
    │                                    → LogStream raises StopAsyncIteration
    │
    └─► _cleanup_machine(app_name, machine_id)  → destroy VM
```
