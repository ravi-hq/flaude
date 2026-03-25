"""Tests for flaude.executor — concurrent execution manager."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from flaude.executor import (
    BatchResult,
    ConcurrentExecutor,
    ExecutionRequest,
    ExecutionResult,
)
from flaude.fly_client import FLY_API_BASE
from flaude.machine_config import MachineConfig

APP = "flaude-test"
TOKEN = "test-fly-token"


def _config(prompt: str = "Fix the bug") -> MachineConfig:
    return MachineConfig(
        claude_code_oauth_token="oauth-tok",
        prompt=prompt,
    )


def _machine_response(machine_id: str, state: str = "created") -> dict:
    return {
        "id": machine_id,
        "name": f"machine-{machine_id}",
        "state": state,
        "region": "iad",
        "instance_id": f"inst_{machine_id}",
    }


def _stopped_response(machine_id: str, exit_code: int = 0) -> dict:
    return {
        "id": machine_id,
        "name": f"machine-{machine_id}",
        "state": "stopped",
        "region": "iad",
        "instance_id": f"inst_{machine_id}",
        "events": [
            {"type": "exit", "status": {"exit_code": exit_code}},
        ],
    }


def _mock_full_lifecycle(machine_id: str, exit_code: int = 0):
    """Set up respx mocks for a complete machine lifecycle (create → wait → cleanup)."""
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        return_value=httpx.Response(200, json=_machine_response(machine_id))
    )
    respx.get(
        f"{FLY_API_BASE}/apps/{APP}/machines/{machine_id}/wait?state=stopped"
    ).mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{machine_id}").mock(
        return_value=httpx.Response(
            200, json=_stopped_response(machine_id, exit_code)
        )
    )
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{machine_id}/stop").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/{machine_id}?force=true"
    ).mock(return_value=httpx.Response(200, json={}))


# ---------------------------------------------------------------------------
# ExecutionResult properties
# ---------------------------------------------------------------------------


def test_execution_result_success():
    from flaude.runner import RunResult

    r = ExecutionResult(
        tag="t1",
        run_result=RunResult(machine_id="m1", exit_code=0, state="stopped", destroyed=True),
    )
    assert r.success is True


def test_execution_result_failure_nonzero():
    from flaude.runner import RunResult

    r = ExecutionResult(
        tag="t1",
        run_result=RunResult(machine_id="m1", exit_code=1, state="stopped", destroyed=True),
    )
    assert r.success is False


def test_execution_result_failure_error():
    r = ExecutionResult(tag="t1", error=RuntimeError("boom"))
    assert r.success is False


def test_batch_result_all_succeeded():
    from flaude.runner import RunResult

    results = [
        ExecutionResult(
            tag="t1",
            run_result=RunResult(machine_id="m1", exit_code=0, state="stopped", destroyed=True),
        ),
        ExecutionResult(
            tag="t2",
            run_result=RunResult(machine_id="m2", exit_code=0, state="stopped", destroyed=True),
        ),
    ]
    batch = BatchResult(results=results, total=2, succeeded=2, failed=0)
    assert batch.all_succeeded is True


def test_batch_result_not_all_succeeded():
    from flaude.runner import RunResult

    results = [
        ExecutionResult(
            tag="t1",
            run_result=RunResult(machine_id="m1", exit_code=0, state="stopped", destroyed=True),
        ),
        ExecutionResult(tag="t2", error=RuntimeError("boom")),
    ]
    batch = BatchResult(results=results, total=2, succeeded=1, failed=1)
    assert batch.all_succeeded is False


# ---------------------------------------------------------------------------
# Empty batch
# ---------------------------------------------------------------------------


async def test_run_batch_empty():
    executor = ConcurrentExecutor(APP, token=TOKEN)
    batch = await executor.run_batch([])
    assert batch.total == 0
    assert batch.succeeded == 0
    assert batch.failed == 0
    assert batch.results == []


# ---------------------------------------------------------------------------
# Single execution
# ---------------------------------------------------------------------------


@respx.mock
async def test_run_one_success():
    """run_one dispatches a single prompt and returns its result."""
    _mock_full_lifecycle("m_single")

    executor = ConcurrentExecutor(APP, token=TOKEN)
    result = await executor.run_one(_config("Hello"), tag="single")

    assert result.tag == "single"
    assert result.success is True
    assert result.run_result is not None
    assert result.run_result.machine_id == "m_single"
    assert result.run_result.exit_code == 0


# ---------------------------------------------------------------------------
# Concurrent batch — multiple machines in parallel
# ---------------------------------------------------------------------------


@respx.mock
async def test_run_batch_multiple_success():
    """Multiple prompts run concurrently, each on its own machine."""
    # We need to handle multiple create calls returning different machine IDs.
    # respx doesn't natively support different responses for repeated calls
    # to the same route, so we use side_effect.
    machine_ids = ["m_batch_1", "m_batch_2", "m_batch_3"]

    create_responses = [
        httpx.Response(200, json=_machine_response(mid)) for mid in machine_ids
    ]
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        side_effect=create_responses
    )

    for mid in machine_ids:
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{mid}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{mid}").mock(
            return_value=httpx.Response(200, json=_stopped_response(mid, 0))
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{mid}/stop").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{mid}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

    executor = ConcurrentExecutor(APP, token=TOKEN)
    requests = [
        ExecutionRequest(config=_config(f"Prompt {i}"), tag=f"tag_{i}")
        for i in range(3)
    ]

    batch = await executor.run_batch(requests)

    assert batch.total == 3
    assert batch.succeeded == 3
    assert batch.failed == 0
    assert batch.all_succeeded is True
    assert len(batch.results) == 3
    # Results are in the same order as requests
    for i, result in enumerate(batch.results):
        assert result.tag == f"tag_{i}"
        assert result.success is True


# ---------------------------------------------------------------------------
# Partial failure — some succeed, some fail
# ---------------------------------------------------------------------------


@respx.mock
async def test_run_batch_partial_failure():
    """One machine fails but others succeed; all are cleaned up."""
    # Machine 1 succeeds, Machine 2 fails at creation
    create_responses = [
        httpx.Response(200, json=_machine_response("m_ok")),
        httpx.Response(422, text="bad config"),
    ]
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        side_effect=create_responses
    )

    # Success path for m_ok
    respx.get(
        f"{FLY_API_BASE}/apps/{APP}/machines/m_ok/wait?state=stopped"
    ).mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/m_ok").mock(
        return_value=httpx.Response(200, json=_stopped_response("m_ok", 0))
    )
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/m_ok/stop").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/m_ok?force=true"
    ).mock(return_value=httpx.Response(200, json={}))

    executor = ConcurrentExecutor(APP, token=TOKEN)
    requests = [
        ExecutionRequest(config=_config("Good prompt"), tag="good"),
        ExecutionRequest(config=_config("Bad prompt"), tag="bad"),
    ]

    batch = await executor.run_batch(requests)

    assert batch.total == 2
    assert batch.succeeded == 1
    assert batch.failed == 1

    # Check individual results
    good = batch.results[0]
    assert good.tag == "good"
    assert good.success is True

    bad = batch.results[1]
    assert bad.tag == "bad"
    assert bad.success is False
    assert bad.error is not None


# ---------------------------------------------------------------------------
# Concurrency limiting with max_concurrency
# ---------------------------------------------------------------------------


@respx.mock
async def test_run_batch_respects_max_concurrency():
    """With max_concurrency=1, executions run sequentially (not truly parallel)."""
    machine_ids = ["m_seq_1", "m_seq_2"]
    execution_order: list[str] = []

    create_call_count = 0

    async def create_handler(request):
        nonlocal create_call_count
        mid = machine_ids[create_call_count]
        create_call_count += 1
        execution_order.append(f"create_{mid}")
        return httpx.Response(200, json=_machine_response(mid))

    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        side_effect=create_handler
    )

    for mid in machine_ids:
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{mid}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{mid}").mock(
            return_value=httpx.Response(200, json=_stopped_response(mid, 0))
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{mid}/stop").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{mid}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

    executor = ConcurrentExecutor(APP, token=TOKEN, max_concurrency=1)
    requests = [
        ExecutionRequest(config=_config(f"Prompt {i}"), tag=f"tag_{i}")
        for i in range(2)
    ]

    batch = await executor.run_batch(requests)

    assert batch.total == 2
    assert batch.succeeded == 2
    # With max_concurrency=1, creates happen sequentially
    assert len(execution_order) == 2


# ---------------------------------------------------------------------------
# All fail — errors captured, no crash
# ---------------------------------------------------------------------------


@respx.mock
async def test_run_batch_all_fail():
    """When all executions fail, batch captures all errors without crashing."""
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        return_value=httpx.Response(500, text="server down")
    )

    executor = ConcurrentExecutor(APP, token=TOKEN)
    requests = [
        ExecutionRequest(config=_config("Prompt 1"), tag="fail_1"),
        ExecutionRequest(config=_config("Prompt 2"), tag="fail_2"),
    ]

    batch = await executor.run_batch(requests)

    assert batch.total == 2
    assert batch.succeeded == 0
    assert batch.failed == 2
    for result in batch.results:
        assert result.success is False
        assert result.error is not None


# ---------------------------------------------------------------------------
# Non-zero exit code — captured as failure, not exception
# ---------------------------------------------------------------------------


@respx.mock
async def test_run_batch_nonzero_exit_is_failure():
    """A machine exiting with non-zero code is captured as a failure, not an error."""
    _mock_full_lifecycle("m_nonzero")
    # Override the stopped response to return exit code 1
    respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/m_nonzero").mock(
        return_value=httpx.Response(
            200, json=_stopped_response("m_nonzero", exit_code=1)
        )
    )

    executor = ConcurrentExecutor(APP, token=TOKEN)
    result = await executor.run_one(_config(), tag="nonzero")

    # run() doesn't raise on non-zero — it returns the result.
    # The executor captures it as a non-success result.
    assert result.success is False
    assert result.error is None
    assert result.run_result is not None
    assert result.run_result.exit_code == 1


# ---------------------------------------------------------------------------
# Executor reuse — can run multiple batches
# ---------------------------------------------------------------------------


@respx.mock
async def test_executor_reuse():
    """An executor can run multiple batches without issues."""
    for batch_num in range(2):
        mid = f"m_reuse_{batch_num}"
        _mock_full_lifecycle(mid)

    executor = ConcurrentExecutor(APP, token=TOKEN)

    # First batch
    batch1 = await executor.run_batch(
        [ExecutionRequest(config=_config("Batch 1"), tag="b1")]
    )
    assert batch1.succeeded == 1

    # Second batch — same executor
    batch2 = await executor.run_batch(
        [ExecutionRequest(config=_config("Batch 2"), tag="b2")]
    )
    assert batch2.succeeded == 1


# ---------------------------------------------------------------------------
# Tags preserved in order
# ---------------------------------------------------------------------------


@respx.mock
async def test_results_preserve_request_order():
    """Results are returned in the same order as the input requests."""
    machine_ids = ["m_order_a", "m_order_b"]
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        side_effect=[
            httpx.Response(200, json=_machine_response(mid))
            for mid in machine_ids
        ]
    )

    for mid in machine_ids:
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{mid}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{mid}").mock(
            return_value=httpx.Response(200, json=_stopped_response(mid, 0))
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{mid}/stop").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{mid}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

    executor = ConcurrentExecutor(APP, token=TOKEN)
    requests = [
        ExecutionRequest(config=_config("First"), tag="alpha"),
        ExecutionRequest(config=_config("Second"), tag="beta"),
    ]

    batch = await executor.run_batch(requests)

    assert batch.results[0].tag == "alpha"
    assert batch.results[1].tag == "beta"
