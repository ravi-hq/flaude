"""Integration tests for concurrent multi-machine execution.

Verifies that multiple prompts execute concurrently on separate machines,
each with a unique machine ID, results stream independently, and all
machines are cleaned up after completion.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

import httpx
import pytest
import respx

from flaude.executor import (
    ConcurrentExecutor,
    ExecutionRequest,
)
from flaude.fly_client import FLY_API_BASE
from flaude.machine_config import MachineConfig
from flaude.runner import RunResult, run

APP = "flaude-integration-test"
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


# ---------------------------------------------------------------------------
# Helpers to track API calls per machine
# ---------------------------------------------------------------------------


class LifecycleTracker:
    """Tracks create/wait/stop/destroy calls per machine to verify isolation and cleanup."""

    def __init__(self):
        self.created_ids: list[str] = []
        self.api_calls: dict[str, list[str]] = defaultdict(list)
        self._create_counter = 0
        self._machine_ids: list[str] = []
        # Track concurrent execution
        self._active_machines: set[str] = set()
        self._max_concurrent: int = 0

    def setup(self, machine_ids: list[str], exit_codes: dict[str, int] | None = None):
        """Register machine IDs and set up respx mocks with tracking callbacks."""
        self._machine_ids = machine_ids
        _exit_codes = exit_codes or {}

        async def create_handler(request):
            mid = self._machine_ids[self._create_counter]
            self._create_counter += 1
            self.created_ids.append(mid)
            self.api_calls[mid].append("create")
            self._active_machines.add(mid)
            self._max_concurrent = max(self._max_concurrent, len(self._active_machines))
            return httpx.Response(200, json=_machine_response(mid))

        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
            side_effect=create_handler
        )

        for mid in machine_ids:
            ec = _exit_codes.get(mid, 0)

            # Wait endpoint
            async def make_wait_handler(m=mid):
                self.api_calls[m].append("wait")
                # Small delay to simulate real wait and allow concurrency
                await asyncio.sleep(0.01)
                return httpx.Response(200, json={})

            respx.get(
                f"{FLY_API_BASE}/apps/{APP}/machines/{mid}/wait?state=stopped"
            ).mock(side_effect=lambda req, m=mid: make_wait_handler(m))

            # GET machine status
            respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{mid}").mock(
                return_value=httpx.Response(
                    200, json=_stopped_response(mid, ec)
                )
            )

            # Stop
            async def stop_handler(request, m=mid):
                self.api_calls[m].append("stop")
                return httpx.Response(200, json={})

            respx.post(
                f"{FLY_API_BASE}/apps/{APP}/machines/{mid}/stop"
            ).mock(side_effect=stop_handler)

            # Destroy
            async def destroy_handler(request, m=mid):
                self.api_calls[m].append("destroy")
                self._active_machines.discard(m)
                return httpx.Response(200, json={})

            respx.delete(
                f"{FLY_API_BASE}/apps/{APP}/machines/{mid}?force=true"
            ).mock(side_effect=destroy_handler)


# ---------------------------------------------------------------------------
# Test: Each prompt gets its own unique machine ID
# ---------------------------------------------------------------------------


@respx.mock
async def test_each_prompt_gets_unique_machine_id():
    """Every prompt in a concurrent batch is dispatched to a separate machine
    with a unique machine ID — no two prompts share a machine."""
    tracker = LifecycleTracker()
    machine_ids = [f"m_unique_{i}" for i in range(5)]
    tracker.setup(machine_ids)

    executor = ConcurrentExecutor(APP, token=TOKEN)
    requests = [
        ExecutionRequest(config=_config(f"Prompt {i}"), tag=f"tag_{i}")
        for i in range(5)
    ]

    batch = await executor.run_batch(requests)

    # All 5 unique IDs were created
    assert len(tracker.created_ids) == 5
    assert len(set(tracker.created_ids)) == 5, "Machine IDs must be unique"

    # Each result has a distinct machine_id
    result_machine_ids = [
        r.run_result.machine_id for r in batch.results if r.run_result
    ]
    assert len(result_machine_ids) == 5
    assert len(set(result_machine_ids)) == 5, "Result machine IDs must be unique"

    # The machine IDs in results match what was created
    assert set(result_machine_ids) == set(machine_ids)


# ---------------------------------------------------------------------------
# Test: Results stream independently per machine
# ---------------------------------------------------------------------------


@respx.mock
async def test_results_stream_independently():
    """Each machine's execution produces independent results — one machine
    failing does not affect others, and results map correctly to their tags."""
    tracker = LifecycleTracker()
    machine_ids = ["m_success_1", "m_fail_1", "m_success_2"]
    exit_codes = {"m_success_1": 0, "m_fail_1": 1, "m_success_2": 0}
    tracker.setup(machine_ids, exit_codes)

    executor = ConcurrentExecutor(APP, token=TOKEN)
    requests = [
        ExecutionRequest(config=_config("Good prompt 1"), tag="good_1"),
        ExecutionRequest(config=_config("Bad prompt"), tag="bad_1"),
        ExecutionRequest(config=_config("Good prompt 2"), tag="good_2"),
    ]

    batch = await executor.run_batch(requests)

    assert batch.total == 3
    assert batch.succeeded == 2
    assert batch.failed == 1

    # Each result independently reflects its machine's exit code
    r0 = batch.results[0]
    assert r0.tag == "good_1"
    assert r0.success is True
    assert r0.run_result.exit_code == 0

    r1 = batch.results[1]
    assert r1.tag == "bad_1"
    assert r1.success is False
    assert r1.run_result.exit_code == 1

    r2 = batch.results[2]
    assert r2.tag == "good_2"
    assert r2.success is True
    assert r2.run_result.exit_code == 0

    # All 3 machines have distinct IDs
    ids = {r.run_result.machine_id for r in batch.results if r.run_result}
    assert len(ids) == 3


# ---------------------------------------------------------------------------
# Test: All machines cleaned up after completion
# ---------------------------------------------------------------------------


@respx.mock
async def test_all_machines_cleaned_up_after_completion():
    """Every machine created in a batch is destroyed after completion,
    regardless of individual success or failure."""
    tracker = LifecycleTracker()
    machine_ids = ["m_cleanup_1", "m_cleanup_2", "m_cleanup_3", "m_cleanup_4"]
    exit_codes = {
        "m_cleanup_1": 0,
        "m_cleanup_2": 1,  # Non-zero exit
        "m_cleanup_3": 0,
        "m_cleanup_4": 137,  # Killed
    }
    tracker.setup(machine_ids, exit_codes)

    executor = ConcurrentExecutor(APP, token=TOKEN)
    requests = [
        ExecutionRequest(config=_config(f"Prompt {i}"), tag=f"tag_{i}")
        for i in range(4)
    ]

    batch = await executor.run_batch(requests)

    assert batch.total == 4

    # Every machine must have had destroy called
    for mid in machine_ids:
        assert "destroy" in tracker.api_calls[mid], (
            f"Machine {mid} was not destroyed"
        )
        assert "stop" in tracker.api_calls[mid], (
            f"Machine {mid} was not stopped before destroy"
        )


# ---------------------------------------------------------------------------
# Test: Cleanup happens even when machines fail at creation
# ---------------------------------------------------------------------------


@respx.mock
async def test_cleanup_on_mixed_creation_failure():
    """Machines that fail to create don't interfere with cleanup of
    successfully created machines."""
    # First machine succeeds, second fails at creation, third succeeds
    mid_1 = "m_mixed_ok_1"
    mid_3 = "m_mixed_ok_2"

    create_call_count = 0
    destroyed_machines: list[str] = []

    async def create_handler(request):
        nonlocal create_call_count
        create_call_count += 1
        if create_call_count == 2:
            # Second machine fails at creation
            return httpx.Response(422, text="bad config")
        mid = mid_1 if create_call_count == 1 else mid_3
        return httpx.Response(200, json=_machine_response(mid))

    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        side_effect=create_handler
    )

    for mid in [mid_1, mid_3]:
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{mid}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{mid}").mock(
            return_value=httpx.Response(200, json=_stopped_response(mid, 0))
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{mid}/stop").mock(
            return_value=httpx.Response(200, json={})
        )

        async def destroy_handler(request, m=mid):
            destroyed_machines.append(m)
            return httpx.Response(200, json={})

        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{mid}?force=true"
        ).mock(side_effect=destroy_handler)

    executor = ConcurrentExecutor(APP, token=TOKEN)
    requests = [
        ExecutionRequest(config=_config("OK 1"), tag="ok_1"),
        ExecutionRequest(config=_config("Fail"), tag="fail"),
        ExecutionRequest(config=_config("OK 2"), tag="ok_2"),
    ]

    batch = await executor.run_batch(requests)

    assert batch.total == 3
    assert batch.succeeded == 2
    assert batch.failed == 1

    # The two successfully created machines must be destroyed
    assert mid_1 in destroyed_machines
    assert mid_3 in destroyed_machines

    # The failed creation has an error, not a run_result
    assert batch.results[1].tag == "fail"
    assert batch.results[1].error is not None
    assert batch.results[1].run_result is None


# ---------------------------------------------------------------------------
# Test: Concurrent execution actually runs in parallel via asyncio.gather
# ---------------------------------------------------------------------------


@respx.mock
async def test_concurrent_execution_runs_in_parallel():
    """Multiple machines execute truly concurrently — verify by checking
    that machines overlap in their active lifetimes."""
    machine_ids = ["m_par_1", "m_par_2", "m_par_3"]
    active_set: set[str] = set()
    max_concurrent = 0

    create_counter = 0

    async def create_handler(request):
        nonlocal create_counter, max_concurrent
        mid = machine_ids[create_counter]
        create_counter += 1
        active_set.add(mid)
        max_concurrent = max(max_concurrent, len(active_set))
        return httpx.Response(200, json=_machine_response(mid))

    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        side_effect=create_handler
    )

    for mid in machine_ids:
        async def wait_handler(request, m=mid):
            # Small delay to keep machine "active" long enough for overlap
            await asyncio.sleep(0.05)
            return httpx.Response(200, json={})

        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{mid}/wait?state=stopped"
        ).mock(side_effect=wait_handler)

        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{mid}").mock(
            return_value=httpx.Response(200, json=_stopped_response(mid, 0))
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{mid}/stop").mock(
            return_value=httpx.Response(200, json={})
        )

        async def destroy_handler(request, m=mid):
            active_set.discard(m)
            return httpx.Response(200, json={})

        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{mid}?force=true"
        ).mock(side_effect=destroy_handler)

    executor = ConcurrentExecutor(APP, token=TOKEN)
    requests = [
        ExecutionRequest(config=_config(f"Prompt {i}"), tag=f"tag_{i}")
        for i in range(3)
    ]

    batch = await executor.run_batch(requests)

    assert batch.total == 3
    assert batch.succeeded == 3
    # With asyncio.gather, machines should have been active concurrently
    assert max_concurrent >= 2, (
        f"Expected concurrent execution but max_concurrent={max_concurrent}"
    )
    # All machines cleaned up
    assert len(active_set) == 0, "All machines should be destroyed after batch"


# ---------------------------------------------------------------------------
# Test: Direct runner.run() invocations execute concurrently with asyncio.gather
# ---------------------------------------------------------------------------


@respx.mock
async def test_direct_run_concurrent_with_gather():
    """Using asyncio.gather with multiple runner.run() calls — each machine
    gets its own ID and all are cleaned up independently."""
    machine_ids = ["m_direct_1", "m_direct_2"]
    destroyed: set[str] = set()
    create_counter = 0

    async def create_handler(request):
        nonlocal create_counter
        mid = machine_ids[create_counter]
        create_counter += 1
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

        async def destroy_handler(request, m=mid):
            destroyed.add(m)
            return httpx.Response(200, json={})

        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{mid}?force=true"
        ).mock(side_effect=destroy_handler)

    # Use runner.run() directly with asyncio.gather
    results = await asyncio.gather(
        run(APP, _config("Prompt A"), token=TOKEN),
        run(APP, _config("Prompt B"), token=TOKEN),
    )

    assert len(results) == 2
    ids = {r.machine_id for r in results}
    assert len(ids) == 2, "Each run() call must get a unique machine ID"
    assert ids == set(machine_ids)

    # Both machines must be destroyed
    assert destroyed == set(machine_ids)


# ---------------------------------------------------------------------------
# Test: Large batch — 10 concurrent machines all cleaned up
# ---------------------------------------------------------------------------


@respx.mock
async def test_large_batch_all_cleaned_up():
    """A larger batch (10 machines) all execute and are destroyed."""
    n = 10
    machine_ids = [f"m_large_{i}" for i in range(n)]
    destroyed: set[str] = set()
    create_counter = 0

    async def create_handler(request):
        nonlocal create_counter
        mid = machine_ids[create_counter]
        create_counter += 1
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

        async def destroy_handler(request, m=mid):
            destroyed.add(m)
            return httpx.Response(200, json={})

        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{mid}?force=true"
        ).mock(side_effect=destroy_handler)

    executor = ConcurrentExecutor(APP, token=TOKEN)
    requests = [
        ExecutionRequest(config=_config(f"Prompt {i}"), tag=f"tag_{i}")
        for i in range(n)
    ]

    batch = await executor.run_batch(requests)

    assert batch.total == n
    assert batch.succeeded == n
    assert batch.all_succeeded is True
    assert destroyed == set(machine_ids), "All 10 machines must be destroyed"

    # Verify unique machine IDs across all results
    result_ids = {r.run_result.machine_id for r in batch.results if r.run_result}
    assert len(result_ids) == n


# ---------------------------------------------------------------------------
# Test: Concurrent machines with varied exit codes stream independently
# ---------------------------------------------------------------------------


@respx.mock
async def test_varied_exit_codes_stream_independently():
    """Each machine exits with a different exit code, and results correctly
    map each exit code to the right tag/machine — proving independent streaming."""
    machine_ids = ["m_ec_0", "m_ec_1", "m_ec_2", "m_ec_42"]
    exit_codes = {"m_ec_0": 0, "m_ec_1": 1, "m_ec_2": 2, "m_ec_42": 42}

    create_counter = 0

    async def create_handler(request):
        nonlocal create_counter
        mid = machine_ids[create_counter]
        create_counter += 1
        return httpx.Response(200, json=_machine_response(mid))

    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        side_effect=create_handler
    )

    for mid in machine_ids:
        ec = exit_codes[mid]
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{mid}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{mid}").mock(
            return_value=httpx.Response(200, json=_stopped_response(mid, ec))
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{mid}/stop").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{mid}?force=true"
        ).mock(return_value=httpx.Response(200, json={}))

    executor = ConcurrentExecutor(APP, token=TOKEN)
    requests = [
        ExecutionRequest(config=_config(f"Prompt ec={ec}"), tag=f"ec_{ec}")
        for ec in [0, 1, 2, 42]
    ]

    batch = await executor.run_batch(requests)

    assert batch.total == 4
    assert batch.succeeded == 1  # Only exit_code 0
    assert batch.failed == 3

    # Verify each result maps to the correct exit code
    expected = [(0, True), (1, False), (2, False), (42, False)]
    for result, (expected_ec, expected_success) in zip(batch.results, expected):
        assert result.run_result is not None
        assert result.run_result.exit_code == expected_ec
        assert result.success is expected_success


# ---------------------------------------------------------------------------
# Test: No interference between concurrent executions on exception
# ---------------------------------------------------------------------------


@respx.mock
async def test_exception_in_one_does_not_affect_others():
    """When one machine's wait raises an API error, other machines still
    complete successfully and all are cleaned up."""
    # Machine 1 succeeds normally, Machine 2 has wait endpoint fail then
    # polling also fails (raising FlyAPIError), Machine 3 succeeds
    machine_ids = ["m_ok_1", "m_error", "m_ok_2"]
    destroyed: set[str] = set()
    create_counter = 0

    async def create_handler(request):
        nonlocal create_counter
        mid = machine_ids[create_counter]
        create_counter += 1
        return httpx.Response(200, json=_machine_response(mid))

    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        side_effect=create_handler
    )

    # OK machines have normal lifecycle
    for mid in ["m_ok_1", "m_ok_2"]:
        respx.get(
            f"{FLY_API_BASE}/apps/{APP}/machines/{mid}/wait?state=stopped"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{mid}").mock(
            return_value=httpx.Response(200, json=_stopped_response(mid, 0))
        )
        respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/{mid}/stop").mock(
            return_value=httpx.Response(200, json={})
        )

        async def ok_destroy(request, m=mid):
            destroyed.add(m)
            return httpx.Response(200, json={})

        respx.delete(
            f"{FLY_API_BASE}/apps/{APP}/machines/{mid}?force=true"
        ).mock(side_effect=ok_destroy)

    # Error machine: wait fails, polling also fails
    respx.get(
        f"{FLY_API_BASE}/apps/{APP}/machines/m_error/wait?state=stopped"
    ).mock(return_value=httpx.Response(500, text="broken"))
    respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/m_error").mock(
        return_value=httpx.Response(500, text="broken")
    )
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/m_error/stop").mock(
        return_value=httpx.Response(200, json={})
    )

    async def error_destroy(request):
        destroyed.add("m_error")
        return httpx.Response(200, json={})

    respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/m_error?force=true"
    ).mock(side_effect=error_destroy)

    executor = ConcurrentExecutor(APP, token=TOKEN)
    requests = [
        ExecutionRequest(config=_config("OK 1"), tag="ok_1"),
        ExecutionRequest(config=_config("Error"), tag="error"),
        ExecutionRequest(config=_config("OK 2"), tag="ok_2"),
    ]

    batch = await executor.run_batch(requests)

    assert batch.total == 3
    assert batch.succeeded == 2
    assert batch.failed == 1

    # The error machine has an exception
    assert batch.results[1].tag == "error"
    assert batch.results[1].error is not None

    # The OK machines completed successfully
    assert batch.results[0].success is True
    assert batch.results[2].success is True

    # ALL machines were destroyed — including the error one
    assert destroyed == {"m_ok_1", "m_error", "m_ok_2"}
