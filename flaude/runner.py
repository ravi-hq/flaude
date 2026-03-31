"""High-level execution flow for flaude — run Claude Code on Fly machines.

Handles the full lifecycle: create machine → wait for exit → destroy machine.
Automatic destruction is guaranteed via try/finally, covering success, failure,
cancellation, and unexpected exceptions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass

from flaude.fly_client import FlyAPIError, fly_get
from flaude.machine import (
    FlyMachine,
    create_machine,
    destroy_machine,
    start_machine,
    stop_machine,
    update_machine,
)
from flaude.machine_config import MachineConfig

logger = logging.getLogger(__name__)

# Polling interval when waiting for machine to reach a terminal state
_POLL_INTERVAL_SECONDS = 2.0

# Terminal states for a Fly machine
_TERMINAL_STATES = frozenset({"stopped", "destroyed", "failed"})

# Regex for the exit-code marker written by entrypoint.sh: [flaude:exit:N]
_EXIT_MARKER_RE = re.compile(r"\[flaude:exit:(\d+)\]")


def extract_exit_code_from_logs(logs: list[str]) -> int | None:
    """Parse the ``[flaude:exit:N]`` marker written by *entrypoint.sh*.

    Scans *logs* in reverse order and returns the first exit code found.
    Returns ``None`` if no marker is present — e.g. when the container was
    killed before the Claude Code process could write it.

    This is used as a fallback when the Fly Machines API does not report
    an exit code (which can happen if the machine is force-destroyed or
    reaches the ``failed`` state without a clean exit).

    Args:
        logs: Log lines collected from the machine's stdout/stderr.

    Returns:
        The integer exit code extracted from ``[flaude:exit:N]``, or
        ``None`` if no such marker is found.
    """
    for line in reversed(logs):
        m = _EXIT_MARKER_RE.search(line)
        if m:
            return int(m.group(1))
    return None


# Regex for the workspace manifest marker: [flaude:manifest:{...}]
_MANIFEST_MARKER_RE = re.compile(r"\[flaude:manifest:(\{.*\})\]")


def extract_workspace_manifest_from_logs(logs: list[str]) -> tuple[str, ...]:
    """Parse the ``[flaude:manifest:{...}]`` marker written by *entrypoint.sh*.

    Scans *logs* for the manifest marker and returns the file list.
    Returns an empty tuple if no marker is found.

    Args:
        logs: Log lines collected from the machine's stdout/stderr.

    Returns:
        A tuple of relative file paths found in the workspace.
    """
    for line in logs:
        m = _MANIFEST_MARKER_RE.search(line)
        if m:
            try:
                data = json.loads(m.group(1))
                return tuple(data.get("files", []))
            except (json.JSONDecodeError, TypeError):
                return ()
    return ()


def _is_failure(exit_code: int | None, state: str) -> bool:
    """Return True if a machine run should be considered a failure.

    A run is a failure when:
    - The exit code is non-zero (e.g. Claude Code returned 1), or
    - The machine reached the ``failed`` state, regardless of exit code
      (this covers OOM kills, entrypoint crashes, etc. where the Fly API
      may not populate the exit code field).
    """
    if exit_code is not None and exit_code != 0:
        return True
    if state == "failed":
        return True
    return False


class MachineExitError(Exception):
    """Raised when a Fly machine exits with a non-zero exit code or failure state.

    Attributes:
        machine_id: The Fly machine ID.
        exit_code: Process exit code (non-zero or None).
        state: Final machine state (e.g. ``stopped``, ``failed``).
        logs: Available log lines captured before/during failure.
            May be empty if no log drain was configured.
    """

    def __init__(
        self,
        machine_id: str,
        exit_code: int | None,
        state: str,
        logs: list[str] | None = None,
    ):
        self.machine_id = machine_id
        self.exit_code = exit_code
        self.state = state
        self.logs = logs or []

        # Build message with log tail if available
        msg = f"Machine {machine_id} exited with code={exit_code} state={state}"
        if self.logs:
            # Include last N lines in the message for quick debugging
            tail = self.logs[-20:]
            log_text = "\n".join(tail)
            if len(self.logs) > 20:
                msg += f"\n\nLast 20 of {len(self.logs)} log lines:\n{log_text}"
            else:
                msg += f"\n\nCaptured {len(self.logs)} log lines:\n{log_text}"
        super().__init__(msg)


@dataclass(frozen=True)
class RunResult:
    """Result of a completed flaude execution.

    Attributes:
        machine_id: The Fly machine ID that ran the task.
        exit_code: Process exit code (0 = success).
        state: Final machine state (e.g. ``stopped``, ``failed``).
        destroyed: Whether the machine was successfully destroyed.
        workspace_files: Tuple of relative file paths in the workspace.
    """

    machine_id: str
    exit_code: int | None
    state: str
    destroyed: bool
    workspace_files: tuple[str, ...] = ()


async def wait_for_machine_exit(
    app_name: str,
    machine_id: str,
    *,
    token: str | None = None,
    poll_interval: float = _POLL_INTERVAL_SECONDS,
    timeout: float = 3600.0,
) -> tuple[str, int | None]:
    """Poll a Fly machine until it reaches a terminal state.

    Uses the Fly Machines API ``GET /machines/{id}/wait`` endpoint first,
    falling back to polling ``GET /machines/{id}`` if wait is unavailable.

    Args:
        app_name: The Fly app the machine belongs to.
        machine_id: The machine to wait on.
        token: Explicit API token.
        poll_interval: Seconds between poll attempts (fallback only).
        timeout: Maximum seconds to wait before giving up.

    Returns:
        A tuple of (final_state, exit_code). exit_code may be None if
        the machine was destroyed before we could read it.

    Raises:
        asyncio.TimeoutError: If the machine doesn't exit within *timeout*.
    """
    # Try the blocking wait endpoint first — it long-polls until the machine
    # reaches the requested state, which is more efficient than polling.
    try:
        await fly_get(
            f"/apps/{app_name}/machines/{machine_id}/wait?state=stopped",
            token=token,
            timeout=timeout,
        )
        # Wait succeeded — now fetch final state to get exit code
        data = await fly_get(
            f"/apps/{app_name}/machines/{machine_id}",
            token=token,
        )
        if data and isinstance(data, dict):
            state = data.get("state", "unknown")
            exit_code = _extract_exit_code(data)
            return state, exit_code
        return "stopped", None
    except FlyAPIError:
        # Wait endpoint failed — fall back to polling
        logger.debug("Wait endpoint failed for %s, falling back to polling", machine_id)
    except TimeoutError:
        raise

    # Fallback: poll GET /machines/{id}
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError(f"Machine {machine_id} did not exit within {timeout}s")

        try:
            data = await fly_get(
                f"/apps/{app_name}/machines/{machine_id}",
                token=token,
            )
        except FlyAPIError as exc:
            if exc.status_code == 404:
                # Machine already gone — treat as destroyed
                return "destroyed", None
            raise

        if data and isinstance(data, dict):
            state = data.get("state", "unknown")
            if state in _TERMINAL_STATES:
                exit_code = _extract_exit_code(data)
                return state, exit_code

        await asyncio.sleep(poll_interval)


def _extract_exit_code(data: dict) -> int | None:
    """Extract exit code from machine status response, if available.

    The Fly Machines API stores exit info in the event's ``request`` field:
    ``event["request"]["exit_event"]["exit_code"]`` or
    ``event["request"]["monitor_event"]["exit_event"]["exit_code"]``.
    """
    events = data.get("events", [])
    for event in reversed(events):
        if event.get("type") == "exit":
            request = event.get("request")
            if request and isinstance(request, dict):
                # Priority 1: monitor_event.exit_event.exit_code
                monitor = request.get("monitor_event")
                if isinstance(monitor, dict):
                    exit_evt = monitor.get("exit_event")
                    if (
                        isinstance(exit_evt, dict)
                        and exit_evt.get("exit_code") is not None
                    ):
                        return int(exit_evt["exit_code"])
                # Priority 2: exit_event.exit_code
                exit_evt = request.get("exit_event")
                if isinstance(exit_evt, dict) and exit_evt.get("exit_code") is not None:
                    return int(exit_evt["exit_code"])
    return None


async def _cleanup_machine(
    app_name: str,
    machine_id: str,
    *,
    token: str | None = None,
) -> bool:
    """Best-effort stop + destroy a machine. Returns True if successful."""
    try:
        await stop_machine(app_name, machine_id, token=token)
    except Exception:
        logger.warning(
            "Failed to stop machine %s during cleanup (continuing to destroy)",
            machine_id,
            exc_info=True,
        )

    try:
        await destroy_machine(app_name, machine_id, token=token)
        return True
    except Exception:
        logger.error(
            "Failed to destroy machine %s — potential orphaned resource!",
            machine_id,
            exc_info=True,
        )
        return False


async def run(
    app_name: str,
    config: MachineConfig,
    *,
    name: str | None = None,
    token: str | None = None,
    wait_timeout: float = 3600.0,
) -> RunResult:
    """Execute a Claude Code prompt on a Fly machine with guaranteed cleanup.

    Creates a Fly machine, waits for the Claude Code process to exit, and
    **always** destroys the machine afterwards — regardless of success, failure,
    or cancellation.

    Args:
        app_name: The Fly app to run in.
        config: Machine configuration including prompt, repos, credentials.
        name: Optional human-readable machine name.
        token: Explicit Fly API token.
        wait_timeout: Max seconds to wait for machine to exit.

    Returns:
        A :class:`RunResult` with exit details.

    Raises:
        FlyAPIError: If machine creation fails.
        TimeoutError: If the machine doesn't exit within *wait_timeout*.
    """
    machine: FlyMachine | None = None
    destroyed = False
    state: str = ""
    exit_code: int | None = None

    try:
        machine = await create_machine(app_name, config, name=name, token=token)
        logger.info("Machine %s created, waiting for exit…", machine.id)

        state, exit_code = await wait_for_machine_exit(
            app_name,
            machine.id,
            token=token,
            timeout=wait_timeout,
        )

        logger.info(
            "Machine %s exited: state=%s exit_code=%s",
            machine.id,
            state,
            exit_code,
        )
    finally:
        if machine is not None:
            logger.info("Destroying machine %s (finally block)", machine.id)
            destroyed = await _cleanup_machine(app_name, machine.id, token=token)
            logger.info(
                "Machine %s cleanup %s",
                machine.id,
                "succeeded" if destroyed else "FAILED",
            )

    return RunResult(
        machine_id=machine.id,
        exit_code=exit_code,
        state=state,
        destroyed=destroyed,
    )


async def run_and_destroy(
    app_name: str,
    config: MachineConfig,
    *,
    name: str | None = None,
    token: str | None = None,
    wait_timeout: float = 3600.0,
    raise_on_failure: bool = True,
) -> RunResult:
    """Execute a Claude Code prompt with automatic cleanup, optionally raising on
    failure.

    This is the recommended entry point. It wraps :func:`run` and optionally
    raises :class:`MachineExitError` when the process exits with a non-zero
    code.

    Args:
        app_name: The Fly app to run in.
        config: Machine configuration.
        name: Optional machine name.
        token: Explicit Fly API token.
        wait_timeout: Max seconds to wait for exit.
        raise_on_failure: If True (default), raise on non-zero exit codes.

    Returns:
        A :class:`RunResult` with exit details.
    """
    result = await run(
        app_name,
        config,
        name=name,
        token=token,
        wait_timeout=wait_timeout,
    )

    if raise_on_failure and _is_failure(result.exit_code, result.state):
        raise MachineExitError(
            machine_id=result.machine_id,
            exit_code=result.exit_code,
            state=result.state,
        )

    return result


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
