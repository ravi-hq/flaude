"""Tests for flaude.hibernate — hibernate_session() and wake_session().

All Fly.io API calls are mocked with respx.  The snapshot backend is an
AsyncMock so we can assert on call order and arguments without real S3 I/O.

Volume backup / restore stubs
------------------------------
flaude.hibernate exposes ``_create_volume_tar`` and ``_restore_volume_tar``
as replaceable module-level callables.  Tests replace them with no-op
coroutines via monkeypatch so the tests focus on orchestration logic
(Fly API ordering, atomicity, returned dataclass fields).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

import flaude.hibernate as _hib
from flaude.fly_client import FLY_API_BASE
from flaude.hibernate import HibernatedSession, hibernate_session, wake_session
from flaude.session import Session
from flaude.snapshot import SnapshotRef

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP = "flaude-test"
TOKEN = "test-fly-token"
MACHINE_ID = "m_hibernate01"
VOLUME_ID = "vol_hibernate01"
NEW_VOLUME_ID = "vol_new01"
NEW_MACHINE_ID = "m_new01"
SESSION_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
REGION = "iad"

SNAPSHOT = SnapshotRef(
    backend="s3",
    uri=f"s3://test-bucket/snaps/{SESSION_ID}.tar.gz",
    size_bytes=1024,
    checksum="a" * 64,
    created_at="2026-04-09T00:00:00+00:00",
)

HIBERNATED = HibernatedSession(
    session_id=SESSION_ID,
    snapshot=SNAPSHOT,
    app_name=APP,
    region=REGION,
    hibernated_at="2026-04-09T00:00:00+00:00",
)


def _live_session() -> Session:
    return Session(
        session_id=SESSION_ID,
        machine_id=MACHINE_ID,
        volume_id=VOLUME_ID,
        app_name=APP,
        region=REGION,
        created_at=datetime.now(UTC).isoformat(),
    )


def _volume_response(volume_id: str = NEW_VOLUME_ID) -> dict:
    return {
        "id": volume_id,
        "name": f"session-{SESSION_ID[:8]}",
        "region": REGION,
        "size_gb": 1,
        "state": "created",
    }


def _machine_response(machine_id: str = NEW_MACHINE_ID) -> dict:
    return {
        "id": machine_id,
        "name": "session-machine",
        "state": "created",
        "region": REGION,
        "instance_id": "inst_001",
    }


def _mock_snapshot_backend(
    upload_result: SnapshotRef = SNAPSHOT,
) -> AsyncMock:
    """Return a mock SnapshotBackend."""
    backend = AsyncMock()
    backend.upload.return_value = upload_result
    backend.download.return_value = None
    backend.delete.return_value = None
    return backend


# ---------------------------------------------------------------------------
# hibernate_session — happy path
# ---------------------------------------------------------------------------


@respx.mock
async def test_hibernate_stops_machine_before_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Machine is stopped before the snapshot upload (correct ordering)."""
    call_order: list[str] = []

    stop_route = respx.post(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
    ).mock(return_value=httpx.Response(200, json={}))

    respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
    ).mock(return_value=httpx.Response(200, json={}))
    respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/volumes/{VOLUME_ID}"
    ).mock(return_value=httpx.Response(200, json={}))

    backend = _mock_snapshot_backend()

    # Patch out volume I/O
    async def _noop_tar(session: Any, tar_path: str) -> None:
        pass

    monkeypatch.setattr(_hib, "_create_volume_tar", _noop_tar)

    async def _upload_with_order(**kwargs: Any) -> SnapshotRef:
        assert stop_route.called, "upload called before machine was stopped"
        call_order.append("upload")
        return SNAPSHOT

    backend.upload = _upload_with_order

    result = await hibernate_session(_live_session(), snapshot_backend=backend, token=TOKEN)

    assert call_order == ["upload"]
    assert result.session_id == SESSION_ID


@respx.mock
async def test_hibernate_destroys_resources_after_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Machine and volume are destroyed only after a successful upload."""
    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
    ).mock(return_value=httpx.Response(200, json={}))

    destroy_machine_route = respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
    ).mock(return_value=httpx.Response(200, json={}))
    destroy_volume_route = respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/volumes/{VOLUME_ID}"
    ).mock(return_value=httpx.Response(200, json={}))

    backend = _mock_snapshot_backend()

    async def _noop_tar(session: Any, tar_path: str) -> None:
        pass

    monkeypatch.setattr(_hib, "_create_volume_tar", _noop_tar)

    await hibernate_session(_live_session(), snapshot_backend=backend, token=TOKEN)

    assert destroy_machine_route.called
    assert destroy_volume_route.called
    backend.upload.assert_awaited_once()


@respx.mock
async def test_hibernate_returns_hibernated_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """hibernate_session() returns a HibernatedSession with correct fields."""
    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
    ).mock(return_value=httpx.Response(200, json={}))
    respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
    ).mock(return_value=httpx.Response(200, json={}))
    respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/volumes/{VOLUME_ID}"
    ).mock(return_value=httpx.Response(200, json={}))

    backend = _mock_snapshot_backend()

    async def _noop_tar(session: Any, tar_path: str) -> None:
        pass

    monkeypatch.setattr(_hib, "_create_volume_tar", _noop_tar)

    result = await hibernate_session(_live_session(), snapshot_backend=backend, token=TOKEN)

    assert isinstance(result, HibernatedSession)
    assert result.session_id == SESSION_ID
    assert result.snapshot == SNAPSHOT
    assert result.app_name == APP
    assert result.region == REGION
    assert "T" in result.hibernated_at  # ISO 8601


# ---------------------------------------------------------------------------
# hibernate_session — atomicity
# ---------------------------------------------------------------------------


@respx.mock
async def test_hibernate_upload_failure_leaves_fly_resources_intact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the upload fails, machine and volume are NOT destroyed (atomicity)."""
    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
    ).mock(return_value=httpx.Response(200, json={}))

    destroy_machine_route = respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
    ).mock(return_value=httpx.Response(200, json={}))
    destroy_volume_route = respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/volumes/{VOLUME_ID}"
    ).mock(return_value=httpx.Response(200, json={}))

    backend = AsyncMock()
    backend.upload.side_effect = RuntimeError("S3 upload failed")

    async def _noop_tar(session: Any, tar_path: str) -> None:
        pass

    monkeypatch.setattr(_hib, "_create_volume_tar", _noop_tar)

    with pytest.raises(RuntimeError, match="S3 upload failed"):
        await hibernate_session(_live_session(), snapshot_backend=backend, token=TOKEN)

    # Fly resources must NOT have been destroyed
    assert not destroy_machine_route.called
    assert not destroy_volume_route.called


@respx.mock
async def test_hibernate_cleanup_failure_preserves_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If resource cleanup fails after upload, the snapshot is preserved (not deleted)."""
    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}/stop"
    ).mock(return_value=httpx.Response(200, json={}))
    # Simulate a failure when destroying the machine
    respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/machines/{MACHINE_ID}?force=true"
    ).mock(return_value=httpx.Response(500, text="internal error"))
    respx.delete(
        f"{FLY_API_BASE}/apps/{APP}/volumes/{VOLUME_ID}"
    ).mock(return_value=httpx.Response(200, json={}))

    backend = _mock_snapshot_backend()

    async def _noop_tar(session: Any, tar_path: str) -> None:
        pass

    monkeypatch.setattr(_hib, "_create_volume_tar", _noop_tar)

    with pytest.raises(Exception):
        await hibernate_session(_live_session(), snapshot_backend=backend, token=TOKEN)

    # Upload happened; snapshot was NOT deleted
    backend.upload.assert_awaited_once()
    backend.delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# wake_session — happy path
# ---------------------------------------------------------------------------


@respx.mock
async def test_wake_session_creates_volume_and_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """wake_session() creates a new volume and a new machine."""
    from flaude.machine_config import MachineConfig

    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/volumes"
    ).mock(return_value=httpx.Response(200, json=_volume_response()))
    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/machines"
    ).mock(return_value=httpx.Response(200, json=_machine_response()))

    backend = _mock_snapshot_backend()

    async def _noop_restore(volume_id: str, app_name: str, tar_path: str) -> None:
        pass

    monkeypatch.setattr(_hib, "_restore_volume_tar", _noop_restore)

    config = MachineConfig(
        claude_code_oauth_token="tok",
        prompt="Resume the session",
    )

    session = await wake_session(
        HIBERNATED, config=config, snapshot_backend=backend, token=TOKEN
    )

    assert session.volume_id == NEW_VOLUME_ID
    assert session.machine_id == NEW_MACHINE_ID
    assert session.app_name == APP


@respx.mock
async def test_wake_session_preserves_session_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """wake_session() preserves the original session_id for conversation continuity."""
    from flaude.machine_config import MachineConfig

    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/volumes"
    ).mock(return_value=httpx.Response(200, json=_volume_response()))
    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/machines"
    ).mock(return_value=httpx.Response(200, json=_machine_response()))

    backend = _mock_snapshot_backend()

    async def _noop_restore(volume_id: str, app_name: str, tar_path: str) -> None:
        pass

    monkeypatch.setattr(_hib, "_restore_volume_tar", _noop_restore)

    config = MachineConfig(
        claude_code_oauth_token="tok",
        prompt="Resume the session",
    )

    session = await wake_session(
        HIBERNATED, config=config, snapshot_backend=backend, token=TOKEN
    )

    assert session.session_id == SESSION_ID


@respx.mock
async def test_wake_session_downloads_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """wake_session() calls backend.download() with the snapshot ref."""
    from flaude.machine_config import MachineConfig

    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/volumes"
    ).mock(return_value=httpx.Response(200, json=_volume_response()))
    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/machines"
    ).mock(return_value=httpx.Response(200, json=_machine_response()))

    backend = _mock_snapshot_backend()

    async def _noop_restore(volume_id: str, app_name: str, tar_path: str) -> None:
        pass

    monkeypatch.setattr(_hib, "_restore_volume_tar", _noop_restore)

    config = MachineConfig(
        claude_code_oauth_token="tok",
        prompt="Resume the session",
    )

    await wake_session(HIBERNATED, config=config, snapshot_backend=backend, token=TOKEN)

    backend.download.assert_awaited_once()
    # The ref passed to download should be our snapshot
    download_call = backend.download.await_args
    assert download_call.args[0] == SNAPSHOT


@respx.mock
async def test_wake_session_does_not_delete_snapshot_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If wake fails (e.g. machine creation error), snapshot is NOT deleted."""
    from flaude.machine_config import MachineConfig

    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/volumes"
    ).mock(return_value=httpx.Response(200, json=_volume_response()))
    # Simulate machine creation failure
    respx.post(
        f"{FLY_API_BASE}/apps/{APP}/machines"
    ).mock(return_value=httpx.Response(500, text="internal error"))

    backend = _mock_snapshot_backend()

    async def _noop_restore(volume_id: str, app_name: str, tar_path: str) -> None:
        pass

    monkeypatch.setattr(_hib, "_restore_volume_tar", _noop_restore)

    config = MachineConfig(
        claude_code_oauth_token="tok",
        prompt="Resume the session",
    )

    with pytest.raises(Exception):
        await wake_session(HIBERNATED, config=config, snapshot_backend=backend, token=TOKEN)

    # Snapshot must NOT have been deleted
    backend.delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# Default volume backup / restore stubs
# ---------------------------------------------------------------------------


async def test_default_create_volume_tar_creates_valid_archive() -> None:
    """_default_create_volume_tar() creates a valid (empty) .tar.gz archive."""
    import tarfile
    import tempfile

    from flaude.hibernate import _default_create_volume_tar

    session = _live_session()
    with tempfile.TemporaryDirectory() as tmp:
        tar_path = f"{tmp}/test.tar.gz"
        await _default_create_volume_tar(session, tar_path)
        assert tarfile.is_tarfile(tar_path)


async def test_default_restore_volume_tar_is_noop() -> None:
    """_default_restore_volume_tar() completes without error (no-op stub)."""
    import tempfile

    from flaude.hibernate import _default_restore_volume_tar

    with tempfile.TemporaryDirectory() as tmp:
        # Should complete without raising
        await _default_restore_volume_tar("vol_test", APP, f"{tmp}/noop.tar.gz")


# ---------------------------------------------------------------------------
# HibernatedSession dataclass
# ---------------------------------------------------------------------------


def test_hibernated_session_is_frozen() -> None:
    """HibernatedSession is immutable (frozen dataclass)."""
    h = HibernatedSession(
        session_id=SESSION_ID,
        snapshot=SNAPSHOT,
        app_name=APP,
        region=REGION,
        hibernated_at="2026-04-09T00:00:00+00:00",
    )
    with pytest.raises((AttributeError, TypeError)):
        h.session_id = "new-id"  # type: ignore[misc]


def test_hibernated_session_fields() -> None:
    """HibernatedSession stores all expected fields."""
    ts = "2026-04-09T12:00:00+00:00"
    h = HibernatedSession(
        session_id=SESSION_ID,
        snapshot=SNAPSHOT,
        app_name=APP,
        region=REGION,
        hibernated_at=ts,
    )
    assert h.session_id == SESSION_ID
    assert h.snapshot == SNAPSHOT
    assert h.app_name == APP
    assert h.region == REGION
    assert h.hibernated_at == ts


# ---------------------------------------------------------------------------
# Integration test (opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not __import__("os").environ.get("FLAUDE_INTEGRATION"),
    reason="Set FLAUDE_INTEGRATION=1 to run real Fly.io round-trip tests",
)
async def test_hibernate_wake_integration() -> None:  # pragma: no cover
    """Real Fly.io + S3 round-trip: create session → hibernate → wake → verify.

    Requires environment variables:
      - FLAUDE_INTEGRATION=1
      - FLY_API_TOKEN
      - CLAUDE_CODE_OAUTH_TOKEN
      - AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (or equivalent)
      - FLAUDE_INTEGRATION_BUCKET (S3 bucket name)
      - FLAUDE_INTEGRATION_APP (Fly app name)
    """
    import os

    from flaude import (
        MachineConfig,
        S3SnapshotBackend,
        create_session,
        destroy_session,
        hibernate_session,
        wake_session,
    )

    bucket = os.environ["FLAUDE_INTEGRATION_BUCKET"]
    app_name = os.environ["FLAUDE_INTEGRATION_APP"]
    fly_token = os.environ["FLY_API_TOKEN"]
    claude_token = os.environ["CLAUDE_CODE_OAUTH_TOKEN"]

    backend = S3SnapshotBackend(bucket=bucket, key_prefix="flaude-integration-test")

    config = MachineConfig(
        claude_code_oauth_token=claude_token,
        prompt="Write the string FLAUDE_SENTINEL to /data/sentinel.txt",
    )

    session, _ = await create_session(app_name, config, token=fly_token)

    try:
        hibernated = await hibernate_session(session, snapshot_backend=backend, token=fly_token)
        assert hibernated.session_id == session.session_id

        resume_config = MachineConfig(
            claude_code_oauth_token=claude_token,
            prompt="Read /data/sentinel.txt and print its contents",
        )

        woken = await wake_session(
            hibernated, config=resume_config, snapshot_backend=backend, token=fly_token
        )
        assert woken.session_id == session.session_id
        assert woken.volume_id != session.volume_id  # new volume

        await destroy_session(app_name, woken, token=fly_token)
    except Exception:
        # Best-effort cleanup; suppress secondary errors
        try:
            await destroy_session(app_name, session, token=fly_token)
        except Exception:
            pass
        raise
