"""Tests for flaude.session — Session dataclass and TTL/expiry logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx

from flaude.fly_client import FLY_API_BASE
from flaude.machine_config import MachineConfig
from flaude.session import Session, create_session, destroy_session

APP = "flaude-test"
TOKEN = "test-fly-token"
MACHINE_ID = "m_session123"
VOLUME_ID = "vol_abc456"
SESSION_ID = "11111111-2222-3333-4444-555555555555"


def _machine_config(**overrides: Any) -> MachineConfig:
    defaults = {
        "claude_code_oauth_token": "oauth-tok",
        "prompt": "Summarize the repo",
    }
    defaults.update(overrides)
    return MachineConfig(**defaults)  # type: ignore[arg-type]


def _machine_response(machine_id: str = MACHINE_ID) -> dict:
    return {
        "id": machine_id,
        "name": "session-machine",
        "state": "created",
        "region": "iad",
        "instance_id": "inst_001",
    }


def _machine_stopped_response(machine_id: str = MACHINE_ID, exit_code: int = 0) -> dict:
    return {
        "id": machine_id,
        "name": "session-machine",
        "state": "stopped",
        "region": "iad",
        "instance_id": "inst_001",
        "events": [
            {
                "type": "exit",
                "status": "stopped",
                "request": {"exit_event": {"exit_code": exit_code}},
            },
        ],
    }


def _volume_response(volume_id: str = VOLUME_ID) -> dict:
    return {
        "id": volume_id,
        "name": "session-vol",
        "region": "iad",
        "size_gb": 1,
        "state": "created",
    }


# ---------------------------------------------------------------------------
# Session dataclass — TTL and expiry
# ---------------------------------------------------------------------------


def test_session_no_ttl_never_expired() -> None:
    """A session with ttl_seconds=0 is never expired."""
    s = Session(
        session_id=SESSION_ID,
        machine_id=MACHINE_ID,
        volume_id=VOLUME_ID,
        app_name=APP,
        region="iad",
        created_at=datetime(2000, 1, 1, tzinfo=UTC).isoformat(),
        ttl_seconds=0,
    )
    assert s.expired is False


def test_session_negative_ttl_never_expired() -> None:
    """Negative ttl_seconds also means no TTL."""
    s = Session(
        session_id=SESSION_ID,
        machine_id=MACHINE_ID,
        volume_id=VOLUME_ID,
        app_name=APP,
        region="iad",
        created_at=datetime(2000, 1, 1, tzinfo=UTC).isoformat(),
        ttl_seconds=-1,
    )
    assert s.expired is False


def test_session_not_yet_expired() -> None:
    """Session with TTL in the future is not expired."""
    future_created = datetime.now(UTC) - timedelta(seconds=30)
    s = Session(
        session_id=SESSION_ID,
        machine_id=MACHINE_ID,
        volume_id=VOLUME_ID,
        app_name=APP,
        region="iad",
        created_at=future_created.isoformat(),
        ttl_seconds=3600,  # 1 hour TTL, only 30s elapsed
    )
    assert s.expired is False


def test_session_expired() -> None:
    """Session past its TTL is expired."""
    old_created = datetime.now(UTC) - timedelta(seconds=120)
    s = Session(
        session_id=SESSION_ID,
        machine_id=MACHINE_ID,
        volume_id=VOLUME_ID,
        app_name=APP,
        region="iad",
        created_at=old_created.isoformat(),
        ttl_seconds=60,  # 60s TTL, 120s elapsed
    )
    assert s.expired is True


def test_session_just_expired() -> None:
    """Session at exactly TTL boundary is expired (elapsed > ttl_seconds)."""
    old_created = datetime.now(UTC) - timedelta(seconds=61)
    s = Session(
        session_id=SESSION_ID,
        machine_id=MACHINE_ID,
        volume_id=VOLUME_ID,
        app_name=APP,
        region="iad",
        created_at=old_created.isoformat(),
        ttl_seconds=60,
    )
    assert s.expired is True


def test_session_is_frozen() -> None:
    """Session dataclass is immutable (frozen=True)."""
    s = Session(
        session_id=SESSION_ID,
        machine_id=MACHINE_ID,
        volume_id=VOLUME_ID,
        app_name=APP,
        region="iad",
        created_at=datetime.now(UTC).isoformat(),
    )
    with pytest.raises((AttributeError, TypeError)):
        s.session_id = "new-id"  # type: ignore[misc]


def test_session_default_ttl_is_zero() -> None:
    """Default ttl_seconds is 0."""
    s = Session(
        session_id=SESSION_ID,
        machine_id=MACHINE_ID,
        volume_id=VOLUME_ID,
        app_name=APP,
        region="iad",
        created_at=datetime.now(UTC).isoformat(),
    )
    assert s.ttl_seconds == 0


# ---------------------------------------------------------------------------
# create_session — happy path
# ---------------------------------------------------------------------------


@respx.mock
async def test_create_session_creates_volume_and_machine() -> None:
    """create_session creates a volume + machine and returns Session + RunResult."""
    respx.post(f"{FLY_API_BASE}/apps/{APP}/volumes").mock(
        return_value=httpx.Response(200, json=_volume_response())
    )
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        return_value=httpx.Response(200, json=_machine_response())
    )
    respx.get(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
    ).mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
        return_value=httpx.Response(200, json=_machine_stopped_response(exit_code=0))
    )

    session, result = await create_session(APP, _machine_config(), token=TOKEN)

    assert session.machine_id == MACHINE_ID
    assert session.volume_id == VOLUME_ID
    assert session.app_name == APP
    assert session.region == "iad"
    assert session.ttl_seconds == 0
    assert result.exit_code == 0
    assert result.state == "stopped"
    assert result.destroyed is False


@respx.mock
async def test_create_session_sets_ttl() -> None:
    """create_session stores ttl_seconds on the returned Session."""
    respx.post(f"{FLY_API_BASE}/apps/{APP}/volumes").mock(
        return_value=httpx.Response(200, json=_volume_response())
    )
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        return_value=httpx.Response(200, json=_machine_response())
    )
    respx.get(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
    ).mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
        return_value=httpx.Response(200, json=_machine_stopped_response())
    )

    session, _ = await create_session(
        APP, _machine_config(), token=TOKEN, ttl_seconds=3600
    )

    assert session.ttl_seconds == 3600


@respx.mock
async def test_create_session_result_not_destroyed() -> None:
    """create_session RunResult always has destroyed=False (machine persists)."""
    respx.post(f"{FLY_API_BASE}/apps/{APP}/volumes").mock(
        return_value=httpx.Response(200, json=_volume_response())
    )
    respx.post(f"{FLY_API_BASE}/apps/{APP}/machines").mock(
        return_value=httpx.Response(200, json=_machine_response())
    )
    respx.get(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/wait?state=stopped"
    ).mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}").mock(
        return_value=httpx.Response(200, json=_machine_stopped_response())
    )

    _, result = await create_session(APP, _machine_config(), token=TOKEN)

    assert result.destroyed is False


# ---------------------------------------------------------------------------
# destroy_session
# ---------------------------------------------------------------------------


@respx.mock
async def test_destroy_session_deletes_machine_and_volume() -> None:
    """destroy_session deletes the machine then the volume."""
    destroy_machine_route = respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
    ).mock(return_value=httpx.Response(200, json={}))
    destroy_volume_route = respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/volumes/{VOLUME_ID}"
    ).mock(return_value=httpx.Response(200, json={}))

    session = Session(
        session_id=SESSION_ID,
        machine_id=MACHINE_ID,
        volume_id=VOLUME_ID,
        app_name=APP,
        region="iad",
        created_at=datetime.now(UTC).isoformat(),
    )

    await destroy_session(APP, session, token=TOKEN)

    assert destroy_machine_route.called
    assert destroy_volume_route.called


@respx.mock
async def test_destroy_session_machine_404_is_ok() -> None:
    """destroy_session tolerates a 404 on the machine (already gone)."""
    respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
    ).mock(return_value=httpx.Response(404, text="not found"))
    respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/volumes/{VOLUME_ID}"
    ).mock(return_value=httpx.Response(200, json={}))

    session = Session(
        session_id=SESSION_ID,
        machine_id=MACHINE_ID,
        volume_id=VOLUME_ID,
        app_name=APP,
        region="iad",
        created_at=datetime.now(UTC).isoformat(),
    )

    # Should not raise
    await destroy_session(APP, session, token=TOKEN)
