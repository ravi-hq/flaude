"""Lifecycle integration — log drain setup/teardown around machine execution.

Wires together the log drain infrastructure and machine lifecycle so that:

1. A ``LogDrainServer`` is started **before** the machine is created.
2. The machine's log queue is subscribed immediately after creation.
3. A ``LogStream`` is returned for the caller to iterate.
4. On machine exit the collector is signalled (sentinel pushed).
5. On machine destruction the drain server is stopped (if we own it).

The primary entry point is :func:`run_with_logs` which returns a
:class:`StreamingRun` — an async-iterable handle that yields log lines
and exposes a ``.result()`` awaitable for the final :class:`RunResult`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from flaude.log_drain import LogCollector, LogDrainServer, LogStream
from flaude.machine import FlyMachine, create_machine
from flaude.machine_config import MachineConfig
from flaude.runner import (
    MachineExitError,
    RunResult,
    _cleanup_machine,
    _is_failure,
    extract_exit_code_from_logs,
    extract_workspace_manifest_from_logs,
    wait_for_machine_exit,
)

logger = logging.getLogger(__name__)


class StreamingRun:
    """Handle for a running machine execution with live log streaming.

    Acts as an async iterator that yields log lines from the machine's
    stdout.  After iteration completes (or on early exit), call
    :meth:`result` to get the :class:`RunResult` with exit details.

    Usage::

        run = await run_with_logs(app, config)
        async for line in run:
            print(line)
        result = await run.result()

    The object can also be used as an async context manager for
    guaranteed cleanup::

        async with await run_with_logs(app, config) as run:
            async for line in run:
                print(line)
        # machine destroyed, server stopped
    """

    def __init__(
        self,
        *,
        log_stream: LogStream,
        result_future: asyncio.Task[RunResult],
        collector: LogCollector,
        server: LogDrainServer | None,
        machine: FlyMachine,
        owns_server: bool,
    ) -> None:
        self._log_stream = log_stream
        self._result_future = result_future
        self._collector = collector
        self._server = server
        self._machine = machine
        self._owns_server = owns_server
        self._cleaned_up = False
        self._collected_logs: list[str] = []

    @property
    def machine_id(self) -> str:
        """The Fly machine ID for this execution."""
        return self._machine.id

    @property
    def log_stream(self) -> LogStream:
        """The underlying :class:`LogStream` instance."""
        return self._log_stream

    @property
    def done(self) -> bool:
        """True if the log stream has finished."""
        return self._log_stream.done

    # -- async iterator protocol ------------------------------------------

    def __aiter__(self) -> StreamingRun:
        return self

    async def __anext__(self) -> str:
        line = await self._log_stream.__anext__()
        self._collected_logs.append(line)
        return line

    # -- async context manager protocol -----------------------------------

    async def __aenter__(self) -> StreamingRun:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.cleanup()

    # -- result & cleanup -------------------------------------------------

    @property
    def collected_logs(self) -> list[str]:
        """Log lines collected so far during async iteration."""
        return list(self._collected_logs)

    async def result(self, *, raise_on_failure: bool = True) -> RunResult:
        """Wait for the machine to exit and return the :class:`RunResult`.

        This also triggers cleanup of the log drain if not already done.

        The exit code is determined from the Fly Machines API response.
        When the API does not populate the exit code (e.g. because the
        machine was force-destroyed or reached ``failed`` state without a
        clean process exit), the collected log lines are searched for a
        ``[flaude:exit:N]`` marker written by *entrypoint.sh*.

        Args:
            raise_on_failure: If True (default), raise :class:`MachineExitError`
                when the machine exits with a non-zero code or a ``failed``
                state, including any collected log lines in the exception.

        Raises:
            MachineExitError: If *raise_on_failure* is True and the run is
                considered a failure (non-zero exit or ``failed`` state).
        """
        try:
            run_result = await self._result_future
        finally:
            await self.cleanup()

        # Use log-based exit code as fallback when the Fly API returns None.
        # entrypoint.sh always writes [flaude:exit:N] before exiting so this
        # gives us the real exit code even when the API response is incomplete.
        effective_exit_code = run_result.exit_code
        if effective_exit_code is None:
            effective_exit_code = extract_exit_code_from_logs(self._collected_logs)

        # Extract workspace manifest from logs
        workspace_files = extract_workspace_manifest_from_logs(self._collected_logs)

        if raise_on_failure and _is_failure(effective_exit_code, run_result.state):
            raise MachineExitError(
                machine_id=run_result.machine_id,
                exit_code=effective_exit_code,
                state=run_result.state,
                logs=self._collected_logs,
            )

        # Return enriched result with workspace files if found
        if workspace_files:
            return RunResult(
                machine_id=run_result.machine_id,
                exit_code=effective_exit_code,
                state=run_result.state,
                destroyed=run_result.destroyed,
                workspace_files=workspace_files,
            )

        return run_result

    async def cleanup(self) -> None:
        """Stop the log drain server (if owned) and release resources.

        Safe to call multiple times — subsequent calls are no-ops.
        """
        if self._cleaned_up:
            return
        self._cleaned_up = True

        if self._owns_server and self._server is not None:
            try:
                await self._server.stop()
                logger.debug("Log drain server stopped (owned by StreamingRun)")
            except Exception:
                logger.warning("Error stopping log drain server", exc_info=True)


async def _wait_signal_destroy(
    app_name: str,
    machine: FlyMachine,
    collector: LogCollector,
    *,
    token: str | None = None,
    wait_timeout: float = 3600.0,
) -> RunResult:
    """Background task: wait for machine exit → signal collector → destroy.

    This runs concurrently while the caller iterates the log stream.
    """
    destroyed = False
    try:
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
        return RunResult(
            machine_id=machine.id,
            exit_code=exit_code,
            state=state,
            destroyed=False,  # updated conceptually after finally
        )
    except Exception:
        # On any error (timeout, API failure, cancellation) we still
        # signal the collector so the log stream terminates.
        raise
    finally:
        # Always signal the collector so the log stream gets its sentinel
        await collector.finish(machine.id)
        logger.debug("Collector signalled finish for machine %s", machine.id)

        # Always destroy the machine
        logger.info("Destroying machine %s (lifecycle finally)", machine.id)
        destroyed = await _cleanup_machine(app_name, machine.id, token=token)
        logger.info(
            "Machine %s cleanup %s",
            machine.id,
            "succeeded" if destroyed else "FAILED",
        )


async def run_with_logs(
    app_name: str,
    config: MachineConfig,
    *,
    name: str | None = None,
    token: str | None = None,
    wait_timeout: float = 3600.0,
    item_timeout: float | None = None,
    total_timeout: float | None = None,
    collector: LogCollector | None = None,
    server: LogDrainServer | None = None,
    server_port: int = 0,
    include_stderr: bool = False,
) -> StreamingRun:
    """Launch a Claude Code execution and return a streaming log handle.

    Sets up log drain infrastructure **before** creating the machine so
    that no early log lines are lost.  The returned :class:`StreamingRun`
    yields log lines as they arrive and guarantees machine cleanup.

    Args:
        app_name: The Fly app to run in.
        config: Machine configuration (prompt, repos, credentials, …).
        name: Optional human-readable machine name.
        token: Explicit Fly API token.
        wait_timeout: Max seconds to wait for machine to exit.
        item_timeout: Per-line timeout for the log stream (``None`` = no limit).
        total_timeout: Overall timeout for the log stream (``None`` = no limit).
        collector: Existing :class:`LogCollector` to reuse (e.g. for concurrent
            runs sharing one server).  If ``None``, a new one is created.
        server: Existing :class:`LogDrainServer` to reuse.  If ``None``, a new
            server is started and will be stopped on cleanup.
        server_port: Port for the auto-created server (0 = auto-assign).
        include_stderr: Whether the log stream should include stderr lines.

    Returns:
        A :class:`StreamingRun` that can be iterated for log lines.

    Raises:
        FlyAPIError: If machine creation fails.
    """
    owns_server = server is None

    # --- 1. Set up log drain infrastructure BEFORE machine creation --------
    if collector is None:
        collector = LogCollector()

    if server is None:
        server = LogDrainServer(
            collector,
            port=server_port,
            include_stderr=include_stderr,
        )
        await server.start()
        logger.info("Log drain server started on port %s", server.actual_port)

    # --- 2. Create the machine --------------------------------------------
    machine: FlyMachine | None = None
    try:
        machine = await create_machine(app_name, config, name=name, token=token)
        logger.info("Machine %s created for streaming run", machine.id)
    except Exception:
        # If machine creation fails and we own the server, clean up
        if owns_server:
            await server.stop()
        raise

    # --- 3. Subscribe to logs for this machine ----------------------------
    queue = await collector.subscribe(machine.id)

    # --- 4. Wrap in a LogStream -------------------------------------------
    log_stream = LogStream(
        queue,
        item_timeout=item_timeout,
        total_timeout=total_timeout,
    )

    # --- 5. Kick off background wait + destroy task -----------------------
    result_task = asyncio.create_task(
        _wait_signal_destroy(
            app_name,
            machine,
            collector,
            token=token,
            wait_timeout=wait_timeout,
        ),
        name=f"flaude-wait-{machine.id}",
    )

    return StreamingRun(
        log_stream=log_stream,
        result_future=result_task,
        collector=collector,
        server=server,
        machine=machine,
        owns_server=owns_server,
    )
