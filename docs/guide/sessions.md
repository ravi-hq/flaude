# Multi-Turn Sessions

By default, flaude machines are **ephemeral** — they run one prompt and are destroyed.
Sessions change that: a machine is stopped (not destroyed) between prompts and restarted
on demand, preserving the full Claude Code conversation on a persistent Fly Volume.

## When to use sessions

Use sessions when:

- You need **multi-turn conversations** — ask Claude Code to do something, review the
  result, then ask a follow-up that builds on what it already knows
- You want **persistent workspace state** — the cloned repo, any files Claude created,
  and the full conversation transcript survive between turns
- You're building **interactive agents** — a chatbot or orchestrator that sends
  sequential prompts to the same Claude Code instance

Use ephemeral machines (`run_and_destroy`) when:

- Each prompt is independent — no conversation history needed
- You're running batch jobs or CI tasks
- You want maximum isolation between runs

## How sessions work

```
Turn 1                              Fly.io
──────                              ──────
create_session() ──► create volume + machine
                         │
                     clone repos, run prompt
                         │
                     stop machine (volume persists)
                         │
                  ◄── Session + RunResult


Turn 2
──────
run_session_turn() ──► update config (new prompt)
                         │
                     start machine
                         │
                     skip clone, --resume session
                         │
                     stop machine
                         │
                    ◄── RunResult


Cleanup
───────
destroy_session() ──► destroy machine + volume
```

Key difference from ephemeral: the machine **stops** instead of being destroyed. The Fly
Volume at `/data` persists the workspace and Claude Code's `CLAUDE_CONFIG_DIR`, so the
next turn picks up where the last one left off.

## Basic usage

### Create a session and run the first prompt

```python
import asyncio
from flaude import MachineConfig, ensure_app, create_session

async def main():
    app = await ensure_app("my-flaude-app")

    config = MachineConfig(
        claude_code_oauth_token="sk-ant-oat-...",
        github_username="you",
        github_token="ghp_...",
        prompt="Review the auth module and list potential security issues.",
        repos=["https://github.com/you/your-repo"],
    )

    session, result = await create_session(app.name, config)
    print(f"Session: {session.session_id}")
    print(f"First turn exit code: {result.exit_code}")
    return session

asyncio.run(main())
```

`create_session` does four things:

1. Creates a 1 GB Fly Volume for persistent storage
2. Creates a machine with the volume mounted at `/data`
3. Runs the first prompt with `--session-id` to initialize the conversation
4. Returns a `Session` handle and the first turn's `RunResult`

### Run follow-up turns

```python
from flaude import MachineConfig, run_session_turn

async def follow_up(session):
    config = MachineConfig(
        claude_code_oauth_token="sk-ant-oat-...",
        prompt="Now fix the top 3 issues you found.",
    )

    result = await run_session_turn(
        session.app_name, session.machine_id, config
    )
    print(f"Turn exit code: {result.exit_code}")
```

`run_session_turn` updates the stopped machine's environment (new prompt, same session
ID), starts it, waits for exit, and leaves it stopped for the next turn. Claude Code
uses `--resume` to continue the conversation.

!!! note
    You don't need `repos` on follow-up turns — the workspace is already populated on the
    volume from the first turn.

### Destroy the session

```python
from flaude import destroy_session

async def cleanup(session):
    await destroy_session(session.app_name, session)
    print("Session destroyed (machine + volume)")
```

Always destroy sessions when you're done. Each session holds a Fly machine and volume
that incur costs.

## Session lifecycle

The `Session` dataclass tracks everything needed to resume:

| Field | Description |
|-------|-------------|
| `session_id` | UUID for the Claude Code conversation |
| `machine_id` | Fly machine ID (stopped between turns) |
| `volume_id` | Fly volume ID (persists workspace + transcripts) |
| `app_name` | Fly app the session belongs to |
| `region` | Fly region for machine + volume |
| `created_at` | ISO 8601 timestamp |
| `ttl_seconds` | Optional time-to-live (0 = no expiry) |

### TTL support

Sessions can have an optional TTL. The caller is responsible for checking and enforcing it:

```python
session, _ = await create_session(
    app.name, config, ttl_seconds=3600  # 1 hour
)

# Later...
if session.expired:
    await destroy_session(app.name, session)
```

!!! warning
    flaude does not run a background reaper. TTL is a passive flag — your code must check
    `session.expired` and call `destroy_session()` when appropriate.

### Volume sizing

The default volume is 1 GB, which is plenty for Claude Code transcripts (KB-scale) and
most repos. Override for large repos:

```python
session, result = await create_session(
    app.name, config, volume_size_gb=5
)
```

## Complete example

```python
import asyncio
from flaude import (
    MachineConfig,
    Session,
    create_session,
    destroy_session,
    ensure_app,
    run_session_turn,
)

async def multi_turn_session():
    app = await ensure_app("my-flaude-app")

    # Turn 1: Analyze the codebase
    config = MachineConfig(
        claude_code_oauth_token="sk-ant-oat-...",
        github_username="you",
        github_token="ghp_...",
        prompt="Analyze src/ for test coverage gaps. List untested functions.",
        repos=["https://github.com/you/your-repo"],
    )

    session, result = await create_session(app.name, config)
    print(f"Analysis complete (exit={result.exit_code})")

    # Turn 2: Write tests for the gaps
    config2 = MachineConfig(
        claude_code_oauth_token="sk-ant-oat-...",
        prompt="Write tests for the top 5 untested functions you found.",
    )

    result2 = await run_session_turn(
        session.app_name, session.machine_id, config2
    )
    print(f"Tests written (exit={result2.exit_code})")

    # Turn 3: Verify the tests pass
    config3 = MachineConfig(
        claude_code_oauth_token="sk-ant-oat-...",
        prompt="Run the test suite and fix any failures.",
    )

    result3 = await run_session_turn(
        session.app_name, session.machine_id, config3
    )
    print(f"Tests verified (exit={result3.exit_code})")

    # Cleanup
    await destroy_session(session.app_name, session)
    print("Session destroyed")

asyncio.run(multi_turn_session())
```

## Performance notes

- **Wake latency**: ~2-3 seconds to restart a stopped machine. Acceptable for
  async/API use cases.
- **Volume I/O**: Fly Volumes are local NVMe — no performance concern for session
  transcripts or typical repos.
- **No concurrent access**: Each session supports one prompt at a time. Queue
  prompts at the caller level if needed.

## API reference

- [`Session`](../api/sessions.md) — session dataclass and lifecycle functions
- [`FlyVolume`](../api/volumes.md) — volume operations
- [`run_session_turn`](../api/execution.md) — execute a session turn
