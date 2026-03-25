"""Concurrent execution manager for flaude.

Launches and tracks multiple Fly.io machine instances simultaneously,
dispatching each prompt to its own machine with guaranteed cleanup of all
resources.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Sequence

from flaude.machine_config import MachineConfig
from flaude.runner import RunResult, run

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutionRequest:
    """A single prompt execution request to be dispatched to a Fly machine.

    Attributes:
        config: Machine configuration (includes prompt, repos, credentials).
        name: Optional human-readable name for the machine.
        tag: Optional user-defined tag for correlating results back to requests.
    """

    config: MachineConfig
    name: str | None = None
    tag: str = ""


@dataclass(frozen=True)
class ExecutionResult:
    """Result of a single execution within a batch.

    Attributes:
        tag: The tag from the corresponding :class:`ExecutionRequest`.
        run_result: The :class:`RunResult` if execution completed (success or failure).
        error: The exception if the execution failed before producing a result.
    """

    tag: str
    run_result: RunResult | None = None
    error: Exception | None = None

    @property
    def success(self) -> bool:
        """True if the execution completed with exit code 0."""
        return (
            self.run_result is not None
            and self.error is None
            and self.run_result.exit_code == 0
        )


@dataclass(frozen=True)
class BatchResult:
    """Aggregated results from a concurrent batch execution.

    Attributes:
        results: Individual results in the same order as the input requests.
        total: Total number of executions.
        succeeded: Count of executions that completed with exit code 0.
        failed: Count of executions that completed with non-zero exit or errored.
    """

    results: list[ExecutionResult]
    total: int = 0
    succeeded: int = 0
    failed: int = 0

    @property
    def all_succeeded(self) -> bool:
        return self.succeeded == self.total


class ConcurrentExecutor:
    """Manages concurrent execution of Claude Code prompts on Fly.io machines.

    Each prompt is dispatched to its own Fly machine. All machines run in
    parallel via asyncio, and cleanup is guaranteed for every machine
    regardless of individual success or failure.

    Args:
        app_name: The Fly app to run machines in.
        token: Explicit Fly API token (falls back to ``FLY_API_TOKEN``).
        max_concurrency: Maximum number of machines to run simultaneously.
            Use ``0`` or ``None`` for unlimited concurrency.
        wait_timeout: Max seconds to wait for each machine to exit.

    Example::

        executor = ConcurrentExecutor("my-fly-app")
        requests = [
            ExecutionRequest(config=MachineConfig(prompt="Fix bug in auth", ...)),
            ExecutionRequest(config=MachineConfig(prompt="Add tests for API", ...)),
        ]
        batch = await executor.run_batch(requests)
        for result in batch.results:
            print(result.tag, result.success)
    """

    def __init__(
        self,
        app_name: str,
        *,
        token: str | None = None,
        max_concurrency: int | None = None,
        wait_timeout: float = 3600.0,
    ) -> None:
        self.app_name = app_name
        self.token = token
        self.max_concurrency = max_concurrency or 0
        self.wait_timeout = wait_timeout

        # Semaphore for concurrency limiting (created lazily per run_batch)
        self._semaphore: asyncio.Semaphore | None = None

    async def _execute_one(
        self,
        request: ExecutionRequest,
    ) -> ExecutionResult:
        """Execute a single request with optional concurrency limiting.

        Never raises — all exceptions are captured in the returned
        :class:`ExecutionResult`.
        """
        try:
            if self._semaphore is not None:
                async with self._semaphore:
                    return await self._run_request(request)
            else:
                return await self._run_request(request)
        except Exception as exc:
            logger.error(
                "Execution failed for tag=%r: %s",
                request.tag,
                exc,
                exc_info=True,
            )
            return ExecutionResult(tag=request.tag, error=exc)

    async def _run_request(
        self,
        request: ExecutionRequest,
    ) -> ExecutionResult:
        """Run a single request through the runner. May raise."""
        logger.info(
            "Starting execution tag=%r name=%r",
            request.tag,
            request.name,
        )

        result = await run(
            self.app_name,
            request.config,
            name=request.name,
            token=self.token,
            wait_timeout=self.wait_timeout,
        )

        logger.info(
            "Execution complete tag=%r machine=%s exit_code=%s",
            request.tag,
            result.machine_id,
            result.exit_code,
        )

        return ExecutionResult(tag=request.tag, run_result=result)

    async def run_batch(
        self,
        requests: Sequence[ExecutionRequest],
    ) -> BatchResult:
        """Execute multiple prompts concurrently, each on its own Fly machine.

        All machines are launched in parallel (subject to *max_concurrency*).
        Each machine is guaranteed to be cleaned up regardless of individual
        success or failure. Results are returned in the same order as the
        input requests.

        Args:
            requests: The execution requests to process.

        Returns:
            A :class:`BatchResult` with per-request results and summary counts.
        """
        if not requests:
            return BatchResult(results=[], total=0, succeeded=0, failed=0)

        # Set up concurrency limiter if configured
        if self.max_concurrency > 0:
            self._semaphore = asyncio.Semaphore(self.max_concurrency)
        else:
            self._semaphore = None

        logger.info(
            "Starting batch of %d executions (max_concurrency=%s)",
            len(requests),
            self.max_concurrency or "unlimited",
        )

        # Launch all tasks concurrently via asyncio.gather.
        # return_exceptions=False because _execute_one never raises —
        # it catches all exceptions internally.
        tasks = [self._execute_one(req) for req in requests]
        results: list[ExecutionResult] = await asyncio.gather(*tasks)

        succeeded = sum(1 for r in results if r.success)
        failed = len(results) - succeeded

        logger.info(
            "Batch complete: %d/%d succeeded, %d failed",
            succeeded,
            len(results),
            failed,
        )

        return BatchResult(
            results=results,
            total=len(results),
            succeeded=succeeded,
            failed=failed,
        )

    async def run_one(
        self,
        config: MachineConfig,
        *,
        name: str | None = None,
        tag: str = "",
    ) -> ExecutionResult:
        """Convenience method to execute a single prompt.

        Equivalent to calling :meth:`run_batch` with a single request.

        Args:
            config: Machine configuration.
            name: Optional machine name.
            tag: Optional tag for result correlation.

        Returns:
            An :class:`ExecutionResult` for the single execution.
        """
        request = ExecutionRequest(config=config, name=name, tag=tag)
        batch = await self.run_batch([request])
        return batch.results[0]
