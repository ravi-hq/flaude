# Serverless Claude Sessions — Implementation Plan

## Overview

Add persistent multi-turn "serverless sessions" to flaude where each session maps to a Fly machine + volume pair. Machines are stopped (not destroyed) between prompts and restarted on demand, preserving Claude Code's conversation state via `--resume` and `CLAUDE_CONFIG_DIR` pointing to a Fly Volume.

## Research Summary

Research conducted by agent team with 4 specialist tracks:
- **Fly API**: `fly_client.py` has `get/post/delete` but no `put`; `machine.py` has `create/get/stop/destroy` but no `start/update`
- **Config/Runner**: `MachineConfig` has `auto_destroy=True` default, no `mounts` field; `runner.py:run()` always destroys in `finally`
- **Entrypoint**: One-shot `claude -p` with no session flags, workspace at `/workspace`, no `CLAUDE_CONFIG_DIR`
- **Models/Tests**: All frozen dataclasses, pytest+respx patterns, zero session/volume concepts exist

### Key Discoveries:
- `wait_for_machine_exit()` (`runner.py:125`) is fully reusable for session turns — no changes needed
- `build_machine_config()` (`machine_config.py:105`) payload structure already supports `mounts` — just need to populate it
- `[flaude:exit:N]` marker pattern (`entrypoint.sh:138`, `runner.py:28`) is the precedent for `[flaude:session:<id>]`
- `restart.policy: "no"` (`machine_config.py:162-164`) is already correct for sessions
- `_parse_machine_response()` (`machine.py:50`) is shared and reusable for new operations

## Current State Analysis

The codebase is strictly ephemeral: create machine → run single prompt → destroy machine. Every execution creates a fresh machine with a fresh filesystem. There are no concepts of volumes, sessions, or machine reuse. The `auto_destroy=True` default and unconditional `_cleanup_machine()` in `runner.py:308-316` enforce this.

## Desired End State

A caller can:
1. **Create a session** — creates a Fly Volume (1GB default) + machine with volume mounted at `/data`, runs first prompt with `--session-id`, stops machine
2. **Resume a session** — updates stopped machine's env vars (new prompt), starts it, runs with `--resume`, stops machine
3. **Destroy a session** — destroys machine + volume

Existing one-shot `run()` / `run_and_destroy()` / `run_with_logs()` remain unchanged.

### Verification:
```python
# First turn
session = await create_session(app_name, config, token=token)
result = await run_session_turn(app_name, session, prompt="Fix the auth bug", token=token)
assert result.exit_code == 0
assert session.session_id  # UUID assigned

# Second turn — conversation continues
result = await run_session_turn(app_name, session, prompt="Now add tests for that fix", token=token)
assert result.exit_code == 0

# Cleanup
await destroy_session(app_name, session, token=token)
```

## What We're NOT Doing

- **Suspend/resume** — `claude -p` exits after each prompt, no process to snapshot. Stop+volume is correct.
- **Concurrent access** — Prompts queue at the caller level, not inside flaude.
- **Token rotation** — `CLAUDE_CODE_OAUTH_TOKEN` is set at machine creation and doesn't rotate between turns.
- **Auto-cleanup timers** — We accept a TTL on the `Session` dataclass but flaude itself does not run a background reaper. The caller is responsible for checking TTL and calling `destroy_session()`.
- **StreamingRun for sessions** — Phase 1 uses `run_session_turn()` (no log streaming). Streaming session support can be added later by adapting `lifecycle.py`.

## File Ownership Map

Designed for parallel execution via `team-implement`:

| File | Phase | Owner Track | Change Type |
|------|-------|-------------|-------------|
| `flaude/fly_client.py` | 1 | backend-api | modify |
| `flaude/machine.py` | 1 | backend-api | modify |
| `flaude/volume.py` | 1 | backend-api | create |
| `flaude/machine_config.py` | 2 | backend-api | modify |
| `flaude/session.py` | 3 | backend-core | create |
| `flaude/runner.py` | 3 | backend-core | modify |
| `flaude/entrypoint.sh` | 4 | entrypoint | modify |
| `flaude/__init__.py` | 5 | backend-core | modify |
| `tests/test_machine.py` | 6 | backend-api | modify |
| `tests/test_volume.py` | 6 | backend-api | create |
| `tests/test_session.py` | 6 | backend-core | create |
| `tests/test_runner.py` | 6 | backend-core | modify |
| `tests/test_entrypoint.py` | 6 | entrypoint | modify |

**Conflict-free guarantee**: No file appears in multiple owner tracks within the same phase. Cross-track dependencies are modeled as phase boundaries.

---

## Phase 1: Fly Client & Volume Primitives

### Overview
Add `fly_put()` to the HTTP layer, `start_machine()` and `update_machine()` to machine operations, and a new `volume.py` module for Fly Volume CRUD.

### Changes Required:

#### 1. `flaude/fly_client.py` — Add `fly_put()`
**File**: `flaude/fly_client.py`
**After line 88** (after `fly_delete`):

```python
async def fly_put(path: str, **kwargs: Any) -> Any:
    return await fly_request("PUT", path, **kwargs)
```

#### 2. `flaude/machine.py` — Add `start_machine()`
**File**: `flaude/machine.py`
**After `destroy_machine` (line 217)**, following the `stop_machine` pattern exactly:

```python
async def start_machine(
    app_name: str,
    machine_id: str,
    *,
    token: str | None = None,
) -> None:
    """Start a stopped machine.

    Best-effort — if the machine is already started or gone, the error
    is suppressed.

    Args:
        app_name: The Fly app the machine belongs to.
        machine_id: The machine ID to start.
        token: Explicit API token (falls back to ``FLY_API_TOKEN``).
    """
    try:
        await fly_post(
            f"/apps/{app_name}/machines/{machine_id}/start",
            token=token,
        )
        logger.info("Start signal sent to machine %s", machine_id)
    except FlyAPIError as exc:
        if exc.status_code in (404, 409):
            logger.debug(
                "Machine %s start returned %s (already started/gone)",
                machine_id,
                exc.status_code,
            )
        else:
            raise
```

#### 3. `flaude/machine.py` — Add `update_machine()`
**After `start_machine`**:

```python
async def update_machine(
    app_name: str,
    machine_id: str,
    config: MachineConfig,
    *,
    name: str | None = None,
    token: str | None = None,
    timeout: float = 60.0,
) -> FlyMachine:
    """Update a stopped machine's configuration.

    Sends a PUT to ``/v1/apps/{app}/machines/{id}`` with the full config
    payload. Used to inject new env vars (prompt, session ID) between
    session turns.

    Args:
        app_name: The Fly app the machine belongs to.
        machine_id: The machine ID to update.
        config: Updated :class:`MachineConfig`.
        name: Optional machine name override.
        token: Explicit API token.
        timeout: HTTP request timeout in seconds.

    Returns:
        A :class:`FlyMachine` with updated state.
    """
    payload = build_machine_config(config)
    if name:
        payload["name"] = name

    logger.info("Updating machine %s in app %r", machine_id, app_name)

    data = await fly_put(
        f"/apps/{app_name}/machines/{machine_id}",
        json=payload,
        token=token,
        timeout=timeout,
    )

    if not data or not isinstance(data, dict):
        raise FlyAPIError(
            status_code=0,
            detail="Empty or invalid response from update-machine endpoint",
            method="PUT",
            url=f"/apps/{app_name}/machines/{machine_id}",
        )

    machine = _parse_machine_response(data, app_name)
    logger.info("Machine %s updated (state=%s)", machine.id, machine.state)
    return machine
```

Update imports at top of `machine.py`:
```python
from flaude.fly_client import FlyAPIError, fly_delete, fly_get, fly_post, fly_put
```

#### 4. `flaude/volume.py` — New module
**File**: `flaude/volume.py` (create)

```python
"""Fly.io volume lifecycle — create, list, and destroy volumes."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from flaude.fly_client import FlyAPIError, fly_delete, fly_get, fly_post

logger = logging.getLogger(__name__)

DEFAULT_VOLUME_SIZE_GB = 1


@dataclass(frozen=True)
class FlyVolume:
    """Represents a Fly.io volume.

    Attributes:
        id: The unique Fly volume ID.
        name: Human-readable volume name.
        region: Fly.io region the volume is in.
        size_gb: Volume size in gigabytes.
        app_name: The Fly app this volume belongs to.
        state: Current volume state.
    """

    id: str
    name: str
    region: str
    size_gb: int
    app_name: str
    state: str


def _parse_volume_response(data: dict, app_name: str) -> FlyVolume:
    """Parse a Fly Volumes API response into a FlyVolume."""
    return FlyVolume(
        id=data["id"],
        name=data.get("name", ""),
        region=data.get("region", ""),
        size_gb=data.get("size_gb", 0),
        app_name=app_name,
        state=data.get("state", "unknown"),
    )


async def create_volume(
    app_name: str,
    *,
    name: str = "flaude_session",
    region: str = "iad",
    size_gb: int = DEFAULT_VOLUME_SIZE_GB,
    token: str | None = None,
) -> FlyVolume:
    """Create a Fly.io volume for session persistence.

    Args:
        app_name: The Fly app to create the volume under.
        name: Volume name (visible in Fly dashboard).
        region: Region for the volume (must match machine region).
        size_gb: Volume size in GB (default 1).
        token: Explicit API token.

    Returns:
        A :class:`FlyVolume` with the volume's ID and metadata.
    """
    payload = {
        "name": name,
        "region": region,
        "size_gb": size_gb,
    }

    logger.info(
        "Creating volume in app %r region=%s size=%dGB",
        app_name,
        region,
        size_gb,
    )

    data = await fly_post(
        f"/apps/{app_name}/volumes",
        json=payload,
        token=token,
    )

    if not data or not isinstance(data, dict):
        raise FlyAPIError(
            status_code=0,
            detail="Empty or invalid response from create-volume endpoint",
            method="POST",
            url=f"/apps/{app_name}/volumes",
        )

    volume = _parse_volume_response(data, app_name)
    logger.info("Volume %s created (region=%s, size=%dGB)", volume.id, volume.region, volume.size_gb)
    return volume


async def list_volumes(
    app_name: str,
    *,
    token: str | None = None,
) -> list[FlyVolume]:
    """List all volumes for a Fly app.

    Args:
        app_name: The Fly app to list volumes for.
        token: Explicit API token.

    Returns:
        List of :class:`FlyVolume` objects.
    """
    data = await fly_get(
        f"/apps/{app_name}/volumes",
        token=token,
    )

    if not data or not isinstance(data, list):
        return []

    return [_parse_volume_response(v, app_name) for v in data]


async def destroy_volume(
    app_name: str,
    volume_id: str,
    *,
    token: str | None = None,
) -> None:
    """Destroy a Fly.io volume permanently.

    Args:
        app_name: The Fly app the volume belongs to.
        volume_id: The volume ID to destroy.
        token: Explicit API token.
    """
    try:
        await fly_delete(
            f"/apps/{app_name}/volumes/{volume_id}",
            token=token,
        )
        logger.info("Volume %s destroyed", volume_id)
    except FlyAPIError as exc:
        if exc.status_code == 404:
            logger.debug("Volume %s already gone (404)", volume_id)
        else:
            raise
```

### Success Criteria:

#### Automated Verification:
- [x] `cd /Users/jake/dev/ravi-hq/flying-claude && uv run ruff check flaude/fly_client.py flaude/machine.py flaude/volume.py`
- [x] `cd /Users/jake/dev/ravi-hq/flying-claude && uv run pytest tests/test_machine.py tests/test_volume.py -x`

#### Manual Verification:
- [x] `fly_put()` follows identical pattern to `fly_get/fly_post/fly_delete`
- [x] `start_machine()` mirrors `stop_machine()` with same error handling
- [x] `update_machine()` mirrors `create_machine()` with `fly_put` instead of `fly_post`
- [x] `FlyVolume` follows `FlyMachine` frozen dataclass pattern

**Gate**: Verify phase 1 passes before proceeding.

---

## Phase 2: Machine Config for Sessions

### Overview
Add volume mount and session ID support to `MachineConfig` and `build_machine_config()`.

### Dependencies
- Requires Phase 1 complete (`FlyVolume` exists for type reference clarity, though we only use `volume_id: str`)

### Changes Required:

#### 1. `flaude/machine_config.py` — Add session fields to `MachineConfig`
**File**: `flaude/machine_config.py`
**Add after `output_format` field (line 102)**:

```python
    # Session support
    volume_id: str = ""
    volume_mount_path: str = "/data"
    session_id: str = ""
```

#### 2. `flaude/machine_config.py` — Wire mounts and session env vars into `build_machine_config()`
**File**: `flaude/machine_config.py`

After the `FLAUDE_OUTPUT_FORMAT` block (line 140), add session env vars:
```python
    if config.session_id:
        env_vars["FLAUDE_SESSION_ID"] = config.session_id
        env_vars["CLAUDE_CONFIG_DIR"] = f"{config.volume_mount_path}/claude"
```

After the metadata block (line 149), add session metadata:
```python
    if config.session_id:
        metadata["session_id"] = config.session_id
```

In the payload construction (after line 165, before the closing `}`), add mounts:
```python
        },
    }

    # Add volume mount for session persistence
    if config.volume_id:
        payload["config"]["mounts"] = [
            {
                "volume": config.volume_id,
                "path": config.volume_mount_path,
            }
        ]

    return payload
```

#### 3. Relax prompt validation for session resume
**File**: `flaude/machine_config.py`

The existing validation at line 120-121 (`if not config.prompt: raise ValueError`) must be relaxed. On session resume, the prompt may be empty if we're just continuing. However, for flaude's model the caller always provides a prompt per turn, so this stays as-is. No change needed.

### Success Criteria:

#### Automated Verification:
- [x] `uv run ruff check flaude/machine_config.py`
- [x] `uv run pytest tests/test_machine_config.py -x`
- [x] Existing tests still pass (new fields have defaults, backward compatible)

#### Manual Verification:
- [x] `build_machine_config()` with `volume_id=""` produces identical payload to before (no `mounts` key)
- [x] `build_machine_config()` with `volume_id="vol_123"` adds `mounts` array
- [x] `build_machine_config()` with `session_id="abc"` adds `FLAUDE_SESSION_ID` + `CLAUDE_CONFIG_DIR` to env vars

**Gate**: Verify phase 2 passes before proceeding.

---

## Phase 3: Session Model & Runner

### Overview
New `session.py` module with `Session` dataclass and `create_session()` / `destroy_session()` helpers. New `run_session_turn()` in `runner.py` that starts a stopped machine, waits for exit, and leaves it stopped (no destroy).

### Dependencies
- Requires Phase 2 complete (volume mount and session config fields)

### Parallel tracks:
- **backend-core**: `session.py` (new) + `runner.py` (modify)
- Phase 4 (entrypoint) can run in parallel — different files

### Changes Required:

#### 1. `flaude/session.py` — New module
**File**: `flaude/session.py` (create)

```python
"""Serverless session management — persistent multi-turn Claude Code sessions.

A session maps to a Fly machine + volume pair. The machine is stopped
between prompts and restarted on demand. Claude Code's conversation
state persists on the Fly Volume via ``CLAUDE_CONFIG_DIR``.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from flaude.machine import (
    FlyMachine,
    create_machine,
    destroy_machine,
    start_machine,
    update_machine,
)
from flaude.machine_config import MachineConfig
from flaude.volume import FlyVolume, create_volume, destroy_volume

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Session:
    """A persistent Claude Code session on Fly.io.

    Tracks the machine, volume, and session metadata needed to
    resume a multi-turn conversation.

    Attributes:
        session_id: UUID identifying the Claude Code session.
        machine_id: Fly machine ID (stopped between turns).
        volume_id: Fly volume ID (persists workspace + session transcripts).
        app_name: Fly app the session belongs to.
        region: Fly region for machine + volume.
        created_at: When the session was created (ISO 8601).
        ttl_seconds: Optional time-to-live in seconds. 0 means no TTL
            (explicit destroy only). Caller is responsible for enforcing.
    """

    session_id: str
    machine_id: str
    volume_id: str
    app_name: str
    region: str
    created_at: str
    ttl_seconds: int = 0

    @property
    def expired(self) -> bool:
        """Check if the session has exceeded its TTL.

        Returns False if ttl_seconds is 0 (no TTL set).
        """
        if self.ttl_seconds <= 0:
            return False
        created = datetime.fromisoformat(self.created_at)
        elapsed = (datetime.now(timezone.utc) - created).total_seconds()
        return elapsed > self.ttl_seconds


async def create_session(
    app_name: str,
    config: MachineConfig,
    *,
    name: str | None = None,
    volume_size_gb: int = 1,
    ttl_seconds: int = 0,
    token: str | None = None,
) -> tuple[Session, "RunResult"]:
    """Create a new session: volume + machine + first prompt.

    Creates a Fly Volume, then a machine with ``auto_destroy=False``
    and the volume mounted at ``/data``. Runs the first prompt using
    ``--session-id`` to pre-assign the UUID. Returns the session
    handle and the first turn's result.

    The machine is left in ``stopped`` state after the first turn.

    Args:
        app_name: Fly app to create the session in.
        config: Machine config (must include ``prompt`` for the first turn).
        name: Optional machine name.
        volume_size_gb: Volume size in GB (default 1).
        ttl_seconds: Optional TTL in seconds (0 = no TTL, explicit destroy only).
        token: Explicit Fly API token.

    Returns:
        A tuple of (Session, RunResult) for the first turn.
    """
    from flaude.runner import RunResult, wait_for_machine_exit

    session_id = str(uuid.uuid4())

    # 1. Create volume
    volume = await create_volume(
        app_name,
        name=f"session-{session_id[:8]}",
        region=config.region,
        size_gb=volume_size_gb,
        token=token,
    )

    # 2. Configure machine for session mode
    config.auto_destroy = False
    config.volume_id = volume.id
    config.volume_mount_path = "/data"
    config.session_id = session_id

    # 3. Create and run machine (first turn)
    machine = await create_machine(app_name, config, name=name, token=token)
    logger.info(
        "Session %s: machine %s created with volume %s",
        session_id,
        machine.id,
        volume.id,
    )

    # 4. Wait for first turn to complete
    state, exit_code = await wait_for_machine_exit(
        app_name, machine.id, token=token
    )

    session = Session(
        session_id=session_id,
        machine_id=machine.id,
        volume_id=volume.id,
        app_name=app_name,
        region=config.region,
        created_at=datetime.now(timezone.utc).isoformat(),
        ttl_seconds=ttl_seconds,
    )

    result = RunResult(
        machine_id=machine.id,
        exit_code=exit_code,
        state=state,
        destroyed=False,
    )

    logger.info(
        "Session %s: first turn complete (state=%s, exit_code=%s)",
        session_id,
        state,
        exit_code,
    )

    return session, result


async def destroy_session(
    app_name: str,
    session: Session,
    *,
    token: str | None = None,
) -> None:
    """Destroy a session — machine and volume.

    Args:
        app_name: Fly app the session belongs to.
        session: The session to destroy.
        token: Explicit Fly API token.
    """
    logger.info("Destroying session %s", session.session_id)
    await destroy_machine(app_name, session.machine_id, token=token)
    await destroy_volume(app_name, session.volume_id, token=token)
    logger.info("Session %s destroyed (machine + volume)", session.session_id)
```

#### 2. `flaude/runner.py` — Add `run_session_turn()`
**File**: `flaude/runner.py`
**Add after `run_and_destroy` (line 368)**, plus add imports at top:

Add to imports (line 16):
```python
from flaude.machine import FlyMachine, create_machine, destroy_machine, start_machine, stop_machine, update_machine
```

New function after `run_and_destroy`:
```python
async def run_session_turn(
    app_name: str,
    machine_id: str,
    config: MachineConfig,
    *,
    token: str | None = None,
    wait_timeout: float = 3600.0,
    raise_on_failure: bool = True,
) -> RunResult:
    """Execute a single turn of a session on an existing stopped machine.

    Updates the machine's config (new prompt, same session ID), starts it,
    waits for the Claude Code process to exit, and leaves the machine in
    ``stopped`` state for the next turn. Does NOT destroy the machine.

    Args:
        app_name: The Fly app the session belongs to.
        machine_id: The stopped machine to resume.
        config: Updated config with new ``prompt`` (must include ``session_id``).
        token: Explicit Fly API token.
        wait_timeout: Max seconds to wait for machine to exit.
        raise_on_failure: If True, raise on non-zero exit.

    Returns:
        A :class:`RunResult` with exit details. ``destroyed`` is always False.
    """
    # 1. Update machine config (injects new FLAUDE_PROMPT + FLAUDE_SESSION_ID)
    await update_machine(app_name, machine_id, config, token=token)

    # 2. Start the stopped machine
    await start_machine(app_name, machine_id, token=token)
    logger.info("Session turn started on machine %s", machine_id)

    # 3. Wait for exit (machine stops itself after claude -p exits)
    state, exit_code = await wait_for_machine_exit(
        app_name,
        machine_id,
        token=token,
        timeout=wait_timeout,
    )

    logger.info(
        "Session turn complete on machine %s: state=%s exit_code=%s",
        machine_id,
        state,
        exit_code,
    )

    result = RunResult(
        machine_id=machine_id,
        exit_code=exit_code,
        state=state,
        destroyed=False,
    )

    if raise_on_failure and _is_failure(result.exit_code, result.state):
        raise MachineExitError(
            machine_id=machine_id,
            exit_code=exit_code,
            state=state,
        )

    return result
```

### Success Criteria:

#### Automated Verification:
- [x] `uv run ruff check flaude/session.py flaude/runner.py`
- [x] `uv run pytest tests/test_runner.py -x` (existing tests still pass)
- [x] `uv run pytest tests/test_session.py -x` (new tests, phase 6)

#### Manual Verification:
- [x] `create_session()` creates volume → machine → waits → returns Session + RunResult
- [x] `run_session_turn()` updates → starts → waits → returns RunResult with `destroyed=False`
- [x] `destroy_session()` destroys machine then volume
- [x] Session.expired returns False when ttl_seconds=0

**Gate**: Verify phase 3 passes. Phase 4 can run in parallel.

---

## Phase 4: Entrypoint Session Support

### Overview
Make `entrypoint.sh` session-aware: detect `FLAUDE_SESSION_ID`, set `CLAUDE_CONFIG_DIR`, skip cloning on subsequent turns, use `--resume` or `--session-id` flags.

### Dependencies
- Independent of phases 1-3 at the code level (entrypoint reads env vars set by machine config)
- Can run in parallel with phase 3

### Changes Required:

#### 1. `flaude/entrypoint.sh` — Session-aware boot
**File**: `flaude/entrypoint.sh`

**Replace line 8** (`WORKSPACE` default):
```bash
# Session mode: workspace lives on the persistent volume at /data/workspace.
# One-shot mode: workspace is ephemeral at /workspace.
if [ -n "${FLAUDE_SESSION_ID:-}" ]; then
    WORKSPACE="${WORKSPACE:-/data/workspace}"
    export CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-/data/claude}"
    mkdir -p "$CLAUDE_CONFIG_DIR" "$WORKSPACE"
    echo "[flaude] Session mode: session_id=$FLAUDE_SESSION_ID"
    echo "[flaude:session:$FLAUDE_SESSION_ID]"
else
    WORKSPACE="${WORKSPACE:-/workspace}"
fi
```

**Replace line 106** (`clone_repos` call) — skip clone if workspace already populated:
```bash
# Run repo cloning (skip if workspace already has content — session resume)
if [ -n "$(ls -A "$WORKSPACE" 2>/dev/null)" ]; then
    echo "[flaude] Workspace already populated, skipping clone (session resume)"
else
    clone_repos
fi
```

**Replace lines 133-134** (claude invocation) — add session flags:
```bash
# Build session arguments
session_args=()
if [ -n "${FLAUDE_SESSION_ID:-}" ]; then
    # Check if this is a resume (session transcript exists) or first turn
    encoded_cwd=$(echo "$PWD" | sed 's|[^a-zA-Z0-9]|-|g')
    session_file="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/${encoded_cwd}/${FLAUDE_SESSION_ID}.jsonl"
    if [ -f "$session_file" ]; then
        session_args+=(--resume "$FLAUDE_SESSION_ID")
        echo "[flaude] Resuming session $FLAUDE_SESSION_ID"
    else
        session_args+=(--session-id "$FLAUDE_SESSION_ID")
        echo "[flaude] Starting new session $FLAUDE_SESSION_ID"
    fi
fi

set +e
claude -p "${output_fmt_args[@]}" "${session_args[@]}" -- "$FLAUDE_PROMPT"
EXIT_CODE=$?
set -e
```

### Success Criteria:

#### Automated Verification:
- [x] `shellcheck flaude/entrypoint.sh` (no errors)
- [x] `uv run pytest tests/test_entrypoint.py -x`

#### Manual Verification:
- [x] Without `FLAUDE_SESSION_ID`: behaves identically to current (workspace=/workspace, no session flags)
- [x] With `FLAUDE_SESSION_ID` on first turn: sets CLAUDE_CONFIG_DIR, uses `--session-id`, emits `[flaude:session:<id>]`
- [x] With `FLAUDE_SESSION_ID` on subsequent turn: skips clone, uses `--resume`, workspace already populated
- [x] `[flaude:session:<id>]` marker appears in logs (parseable by runner)

**Gate**: Verify phase 4 passes before proceeding to phase 5.

---

## Phase 5: Public API & Exports

### Overview
Wire session functions into `__init__.py` so callers can use `from flaude import create_session, run_session_turn, destroy_session, Session`.

### Dependencies
- Requires Phase 3 + Phase 4 complete

### Changes Required:

#### 1. `flaude/__init__.py` — Add session exports
**File**: `flaude/__init__.py`

Add to Primary API imports (after line 8):
```python
from flaude.session import Session, create_session, destroy_session
from flaude.runner import run_session_turn
```

Add to Advanced API imports (after line 29):
```python
from flaude.volume import FlyVolume, create_volume, destroy_volume, list_volumes
from flaude.machine import start_machine, update_machine
```

Add to `__all__` — Primary API section:
```python
    "Session",
    "create_session",
    "destroy_session",
    "run_session_turn",
```

Add to `__all__` — Advanced API section:
```python
    "FlyVolume",
    "create_volume",
    "destroy_volume",
    "list_volumes",
    "start_machine",
    "update_machine",
```

### Success Criteria:

#### Automated Verification:
- [x] `uv run ruff check flaude/__init__.py`
- [x] `uv run python -c "from flaude import create_session, run_session_turn, destroy_session, Session"`
- [x] `uv run python -c "from flaude import FlyVolume, create_volume, start_machine, update_machine"`

**Gate**: Verify imports resolve before proceeding to tests.

---

## Phase 6: Tests

### Overview
Unit tests for all new functions, following existing respx + pytest-asyncio patterns.

### Parallel tracks:
- **backend-api**: `tests/test_machine.py` (add start/update tests), `tests/test_volume.py` (new)
- **backend-core**: `tests/test_session.py` (new), `tests/test_runner.py` (add session turn tests)
- **entrypoint**: `tests/test_entrypoint.py` (add session mode tests)

### Changes Required:

#### 1. `tests/test_machine.py` — Add `start_machine` and `update_machine` tests

```python
# --- start_machine ---

@respx.mock
async def test_start_machine_sends_post() -> None:
    route = respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/m_abc123/start").mock(
        return_value=httpx.Response(200)
    )
    await start_machine(APP, "m_abc123", token=TOKEN)
    assert route.called


@respx.mock
async def test_start_machine_ignores_409_already_started() -> None:
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/m_abc123/start").mock(
        return_value=httpx.Response(409, text="conflict")
    )
    await start_machine(APP, "m_abc123", token=TOKEN)  # should not raise


@respx.mock
async def test_start_machine_ignores_404_gone() -> None:
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/m_abc123/start").mock(
        return_value=httpx.Response(404, text="not found")
    )
    await start_machine(APP, "m_abc123", token=TOKEN)  # should not raise


@respx.mock
async def test_start_machine_raises_on_500() -> None:
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/m_abc123/start").mock(
        return_value=httpx.Response(500, text="server error")
    )
    with pytest.raises(FlyAPIError, match="500"):
        await start_machine(APP, "m_abc123", token=TOKEN)


# --- update_machine ---

@respx.mock
async def test_update_machine_sends_put() -> None:
    route = respx.put(f"{FLY_API_BASE}/apps/{APP}/machines/m_abc123").mock(
        return_value=httpx.Response(200, json=_machine_response())
    )
    config = _machine_config()
    machine = await update_machine(APP, "m_abc123", config, token=TOKEN)
    assert route.called
    assert isinstance(machine, FlyMachine)
```

#### 2. `tests/test_volume.py` — New file

```python
"""Tests for flaude.volume — Fly volume lifecycle operations."""

import httpx
import pytest
import respx

from flaude.fly_client import FLY_API_BASE, FlyAPIError
from flaude.volume import FlyVolume, create_volume, destroy_volume, list_volumes

APP = "flaude-test"
TOKEN = "test-fly-token"


def _volume_response(
    *,
    volume_id: str = "vol_abc123",
    name: str = "test-volume",
    region: str = "iad",
    size_gb: int = 1,
    state: str = "created",
) -> dict:
    return {
        "id": volume_id,
        "name": name,
        "region": region,
        "size_gb": size_gb,
        "state": state,
    }


@respx.mock
async def test_create_volume_returns_fly_volume() -> None:
    route = respx.post(f"{FLY_API_BASE}/apps/{APP}/volumes").mock(
        return_value=httpx.Response(200, json=_volume_response())
    )
    vol = await create_volume(APP, token=TOKEN)
    assert route.called
    assert isinstance(vol, FlyVolume)
    assert vol.id == "vol_abc123"
    assert vol.size_gb == 1


@respx.mock
async def test_destroy_volume_sends_delete() -> None:
    route = respx.delete(f"{FLY_API_BASE}/apps/{APP}/volumes/vol_abc123").mock(
        return_value=httpx.Response(200)
    )
    await destroy_volume(APP, "vol_abc123", token=TOKEN)
    assert route.called


@respx.mock
async def test_destroy_volume_ignores_404() -> None:
    respx.delete(f"{FLY_API_BASE}/apps/{APP}/volumes/vol_abc123").mock(
        return_value=httpx.Response(404, text="not found")
    )
    await destroy_volume(APP, "vol_abc123", token=TOKEN)  # should not raise


@respx.mock
async def test_list_volumes_returns_list() -> None:
    respx.get(f"{FLY_API_BASE}/apps/{APP}/volumes").mock(
        return_value=httpx.Response(200, json=[_volume_response()])
    )
    vols = await list_volumes(APP, token=TOKEN)
    assert len(vols) == 1
    assert vols[0].id == "vol_abc123"
```

#### 3. `tests/test_session.py` — New file
Test `Session` dataclass (TTL, expired property) and integration with mocked Fly API.

#### 4. `tests/test_runner.py` — Add `run_session_turn` tests
Test update → start → wait → result flow with respx mocks.

### Success Criteria:

#### Automated Verification:
- [x] `uv run pytest tests/ -x --ignore=tests/test_e2e.py`
- [x] `uv run ruff check tests/`
- [x] All new tests pass, all existing tests still pass

---

## Testing Strategy

### Automated:
- Unit tests with respx HTTP mocking cover all new Fly API calls (start, update, volume CRUD)
- Session dataclass tests cover TTL/expired logic
- `run_session_turn` tests mock the update → start → wait sequence
- Existing tests remain green (all new fields have defaults)

### Manual Testing Steps:
1. Create a session with a simple prompt against a real Fly app
2. Verify machine is in `stopped` state after first turn
3. Resume with a follow-up prompt referencing the first turn's context
4. Verify Claude Code remembers the conversation (confirms `--resume` + `CLAUDE_CONFIG_DIR` work)
5. Destroy the session and verify machine + volume are cleaned up
6. Test TTL: create session with `ttl_seconds=60`, verify `session.expired` returns True after 60s

## Performance Considerations

- **Wake latency**: ~2-3 seconds cold boot from stopped state per turn. Acceptable for async/API use cases.
- **Volume I/O**: Fly Volumes are local NVMe — no performance concern for session transcripts (KB) or typical repos.
- **Volume size**: 1GB default. Claude transcripts are KB-scale. Git repos are the variable — caller can override `volume_size_gb`.
- **No background reaper**: TTL is a passive flag. The caller checks `session.expired` and decides when to call `destroy_session()`. This avoids complexity inside flaude.

## References

- Research: `thoughts/research/2026-03-30-serverless-claude-sessions.md`
- Fly Machines API: `POST /v1/apps/{app}/machines/{id}/start`, `PUT /v1/apps/{app}/machines/{id}`
- Fly Volumes API: `POST /v1/apps/{app}/volumes`, `DELETE /v1/apps/{app}/volumes/{id}`
- Claude Code session resume: `claude -p --resume <session-id>`, `claude -p --session-id <uuid>`
- `CLAUDE_CONFIG_DIR` env var redirects all Claude storage
- Comparable patterns: `stop_machine()` at `machine.py:157`, `create_machine()` at `machine.py:62`, `run()` at `runner.py:258`
