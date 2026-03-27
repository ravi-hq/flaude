"""Tests for flaude.machine — Fly machine lifecycle operations."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from flaude.fly_client import FLY_API_BASE, FlyAPIError
from flaude.machine import (
    FlyMachine,
    create_machine,
    destroy_machine,
    get_machine,
    stop_machine,
)
from flaude.machine_config import MachineConfig

APP = "flaude-test"
TOKEN = "test-fly-token"


def _machine_config(**overrides: Any) -> MachineConfig:
    defaults = {
        "claude_code_oauth_token": "oauth-tok",
        "prompt": "Fix the bug",
    }
    defaults.update(overrides)
    return MachineConfig(**defaults)  # type: ignore[arg-type]


def _machine_response(
    *,
    machine_id: str = "m_abc123",
    name: str = "test-machine",
    state: str = "created",
    region: str = "iad",
    instance_id: str = "inst_001",
) -> dict:
    return {
        "id": machine_id,
        "name": name,
        "state": state,
        "region": region,
        "instance_id": instance_id,
    }


# ---------------------------------------------------------------------------
# create_machine
# ---------------------------------------------------------------------------


@respx.mock
async def test_create_machine_returns_fly_machine() -> None:
    """create_machine POSTs to the API and returns a FlyMachine."""
    route = respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        return_value=httpx.Response(200, json=_machine_response())
    )

    machine = await create_machine(APP, _machine_config(), token=TOKEN)

    assert isinstance(machine, FlyMachine)
    assert machine.id == "m_abc123"
    assert machine.name == "test-machine"
    assert machine.state == "created"
    assert machine.region == "iad"
    assert machine.instance_id == "inst_001"
    assert machine.app_name == APP
    assert route.called


@respx.mock
async def test_create_machine_sends_correct_payload() -> None:
    """The POST body contains the machine config payload."""
    route = respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        return_value=httpx.Response(200, json=_machine_response())
    )

    cfg = _machine_config(region="lhr")
    await create_machine(APP, cfg, token=TOKEN)

    request = route.calls[0].request
    import json

    body = json.loads(request.content)
    assert body["region"] == "lhr"
    assert body["config"]["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-tok"
    assert body["config"]["env"]["FLAUDE_PROMPT"] == "Fix the bug"


@respx.mock
async def test_create_machine_with_name() -> None:
    """When a name is given it appears in the request payload."""
    route = respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        return_value=httpx.Response(200, json=_machine_response(name="my-box"))
    )

    machine = await create_machine(APP, _machine_config(), name="my-box", token=TOKEN)

    import json

    body = json.loads(route.calls[0].request.content)
    assert body["name"] == "my-box"
    assert machine.name == "my-box"


@respx.mock
async def test_create_machine_raises_on_api_error() -> None:
    """FlyAPIError is raised when the API returns a non-2xx status."""
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        return_value=httpx.Response(422, text="invalid config")
    )

    with pytest.raises(FlyAPIError) as exc_info:
        await create_machine(APP, _machine_config(), token=TOKEN)

    assert exc_info.value.status_code == 422


def test_create_machine_validates_config() -> None:
    """create_machine propagates ValueError from build_machine_config."""
    # This is sync because we expect the error before any await
    # Actually we need to await it — use pytest.raises in an async test


@respx.mock
async def test_create_machine_validates_missing_prompt() -> None:
    """ValueError raised when prompt is empty."""
    cfg = MachineConfig(claude_code_oauth_token="tok", prompt="")
    with pytest.raises(ValueError, match="prompt"):
        await create_machine(APP, cfg, token=TOKEN)


@respx.mock
async def test_create_machine_validates_missing_token() -> None:
    """ValueError raised when oauth token is empty."""
    cfg = MachineConfig(prompt="hello")
    with pytest.raises(ValueError, match="claude_code_oauth_token"):
        await create_machine(APP, cfg, token=TOKEN)


# ---------------------------------------------------------------------------
# get_machine
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_machine_returns_current_state() -> None:
    """get_machine fetches and returns the machine state."""
    respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/m_abc123").mock(
        return_value=httpx.Response(200, json=_machine_response(state="started"))
    )

    machine = await get_machine(APP, "m_abc123", token=TOKEN)
    assert machine.state == "started"
    assert machine.id == "m_abc123"


@respx.mock
async def test_get_machine_raises_on_404() -> None:
    """FlyAPIError raised when machine not found."""
    respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/m_gone").mock(
        return_value=httpx.Response(404, text="not found")
    )

    with pytest.raises(FlyAPIError) as exc_info:
        await get_machine(APP, "m_gone", token=TOKEN)
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# stop_machine
# ---------------------------------------------------------------------------


@respx.mock
async def test_stop_machine_sends_post() -> None:
    """stop_machine POSTs to the stop endpoint."""
    route = respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/m_abc123/stop").mock(
        return_value=httpx.Response(200, json={})
    )

    await stop_machine(APP, "m_abc123", token=TOKEN)
    assert route.called


@respx.mock
async def test_stop_machine_ignores_404() -> None:
    """stop_machine silently ignores 404 (already gone)."""
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/m_gone/stop").mock(
        return_value=httpx.Response(404, text="not found")
    )

    # Should not raise
    await stop_machine(APP, "m_gone", token=TOKEN)


@respx.mock
async def test_stop_machine_ignores_409() -> None:
    """stop_machine silently ignores 409 (already stopped)."""
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/m_stopped/stop").mock(
        return_value=httpx.Response(409, text="conflict")
    )

    await stop_machine(APP, "m_stopped", token=TOKEN)


@respx.mock
async def test_stop_machine_raises_on_other_errors() -> None:
    """stop_machine re-raises non-404/409 errors."""
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/m_abc123/stop").mock(
        return_value=httpx.Response(500, text="server error")
    )

    with pytest.raises(FlyAPIError) as exc_info:
        await stop_machine(APP, "m_abc123", token=TOKEN)
    assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# destroy_machine
# ---------------------------------------------------------------------------


@respx.mock
async def test_destroy_machine_sends_delete_with_force() -> None:
    """destroy_machine sends DELETE with ?force=true by default."""
    route = respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/m_abc123?force=true"
    ).mock(return_value=httpx.Response(200, json={}))

    await destroy_machine(APP, "m_abc123", token=TOKEN)
    assert route.called


@respx.mock
async def test_destroy_machine_without_force() -> None:
    """destroy_machine without force omits the query param."""
    route = respx.delete(f"{FLY_API_BASE}/apps/{APP}/machines/m_abc123").mock(
        return_value=httpx.Response(200, json={})
    )

    await destroy_machine(APP, "m_abc123", force=False, token=TOKEN)
    assert route.called


@respx.mock
async def test_destroy_machine_ignores_404() -> None:
    """destroy_machine silently ignores 404 (already destroyed)."""
    respx.delete(f"{FLY_API_BASE}/apps/{APP}/machines/m_gone?force=true").mock(
        return_value=httpx.Response(404, text="not found")
    )

    await destroy_machine(APP, "m_gone", token=TOKEN)


@respx.mock
async def test_destroy_machine_raises_on_other_errors() -> None:
    """destroy_machine re-raises non-404 errors."""
    respx.delete(f"{FLY_API_BASE}/apps/{APP}/machines/m_abc123?force=true").mock(
        return_value=httpx.Response(500, text="server error")
    )

    with pytest.raises(FlyAPIError) as exc_info:
        await destroy_machine(APP, "m_abc123", token=TOKEN)
    assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# FlyMachine.cleanup
# ---------------------------------------------------------------------------


def _make_machine(
    machine_id: str = "m_abc123",
    state: str = "started",
) -> FlyMachine:
    return FlyMachine(
        id=machine_id,
        name="test-machine",
        state=state,
        region="iad",
        instance_id="inst_001",
        app_name=APP,
    )


@respx.mock
async def test_cleanup_stops_then_destroys() -> None:
    """cleanup() calls stop then destroy in sequence."""
    stop_route = respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/m_abc123/stop").mock(
        return_value=httpx.Response(200, json={})
    )
    destroy_route = respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/m_abc123?force=true"
    ).mock(return_value=httpx.Response(200, json={}))

    machine = _make_machine()
    await machine.cleanup(token=TOKEN)

    assert stop_route.called
    assert destroy_route.called


@respx.mock
async def test_cleanup_handles_already_stopped() -> None:
    """cleanup() succeeds when stop returns 409 (already stopped)."""
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/m_abc123/stop").mock(
        return_value=httpx.Response(409, text="conflict")
    )
    destroy_route = respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/m_abc123?force=true"
    ).mock(return_value=httpx.Response(200, json={}))

    machine = _make_machine()
    await machine.cleanup(token=TOKEN)

    assert destroy_route.called


@respx.mock
async def test_cleanup_handles_already_destroyed() -> None:
    """cleanup() succeeds when both stop and destroy return 404."""
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/m_abc123/stop").mock(
        return_value=httpx.Response(404, text="not found")
    )
    respx.delete(f"{FLY_API_BASE}/apps/{APP}/machines/m_abc123?force=true").mock(
        return_value=httpx.Response(404, text="not found")
    )

    machine = _make_machine()
    # Should not raise
    await machine.cleanup(token=TOKEN)


@respx.mock
async def test_cleanup_propagates_stop_error() -> None:
    """cleanup() re-raises unexpected errors from stop."""
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/m_abc123/stop").mock(
        return_value=httpx.Response(500, text="server error")
    )

    machine = _make_machine()
    with pytest.raises(FlyAPIError) as exc_info:
        await machine.cleanup(token=TOKEN)
    assert exc_info.value.status_code == 500


@respx.mock
async def test_cleanup_propagates_destroy_error() -> None:
    """cleanup() re-raises unexpected errors from destroy."""
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines/m_abc123/stop").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.delete(f"{FLY_API_BASE}/apps/{APP}/machines/m_abc123?force=true").mock(
        return_value=httpx.Response(500, text="server error")
    )

    machine = _make_machine()
    with pytest.raises(FlyAPIError) as exc_info:
        await machine.cleanup(token=TOKEN)
    assert exc_info.value.status_code == 500
