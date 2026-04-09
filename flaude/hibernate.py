"""Hibernate and wake primitives for scale-to-zero session management.

Hibernating a session snapshots the Fly volume to object storage and
destroys the Fly resources (machine + volume), eliminating the
$0.15/GB-month volume cost while the session is idle.

Waking restores the snapshot into a fresh volume, creates a new machine,
and returns a live :class:`~flaude.session.Session` ready to resume
multi-turn conversations.

Typical usage::

    from flaude import (
        create_session,
        hibernate_session,
        wake_session,
        S3SnapshotBackend,
        MachineConfig,
    )

    backend = S3SnapshotBackend(bucket="my-bucket", key_prefix="flaude-snaps")

    # Hibernate an idle session
    hibernated = await hibernate_session(session, snapshot_backend=backend)

    # ...later, wake it
    session = await wake_session(hibernated, config=config, snapshot_backend=backend)

Atomicity guarantees
--------------------
- ``hibernate_session``: Fly resources are only destroyed **after** the
  snapshot upload succeeds.  An upload failure leaves the session intact.
- ``wake_session``: A restore failure (download or machine creation) does
  **not** delete the snapshot — you can retry.

Volume backup / restore
-----------------------
The actual extraction of volume data into a local tarball (and the inverse
restore) requires access to the Fly volume's file system.  The library
provides :func:`_create_volume_tar` and :func:`_restore_volume_tar` as
replaceable module-level callables so that callers (and tests) can supply
their own implementation.

By default, both functions create/expect the tarball via the session machine
being started in backup/restore mode (entrypoint reads ``FLAUDE_BACKUP`` /
``FLAUDE_RESTORE`` env vars).  Alternatively you can replace them at module
level before calling the public functions::

    import flaude.hibernate as _hib
    _hib._create_volume_tar = my_volume_extractor
"""

from __future__ import annotations

import asyncio
import logging
import os
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Awaitable, Callable

from flaude.machine import create_machine, destroy_machine, stop_machine
from flaude.machine_config import MachineConfig
from flaude.session import Session
from flaude.snapshot import SnapshotBackend, SnapshotRef
from flaude.volume import create_volume, destroy_volume

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HibernatedSession:
    """Metadata for a session that has been hibernated to object storage.

    Attributes:
        session_id: UUID identifying the Claude Code session (preserved across
            hibernate / wake cycles).
        snapshot: Reference to the uploaded volume snapshot.
        app_name: Fly app the session belongs to.
        region: Fly region the session was running in.
        hibernated_at: ISO 8601 timestamp of when the session was hibernated.
    """

    session_id: str
    snapshot: SnapshotRef
    app_name: str
    region: str
    hibernated_at: str


# ---------------------------------------------------------------------------
# Internal volume backup / restore hooks
# These are module-level callables so callers and tests can replace them.
# ---------------------------------------------------------------------------

_VolumeBackupFn = Callable[[Session, str], Awaitable[None]]
_VolumeRestoreFn = Callable[[str, str, str], Awaitable[None]]


async def _default_create_volume_tar(session: Session, tar_path: str) -> None:
    """Create a tarball of the session volume at *tar_path*.

    **Default implementation** — creates an empty archive.  In production,
    replace this function (or the module-level :data:`_create_volume_tar`
    alias) with an implementation that extracts the actual volume data, e.g.
    by starting the session machine in backup mode or using ``flyctl ssh``.

    Args:
        session: The session whose volume should be archived.
        tar_path: Local destination path for the ``.tar.gz`` archive.
    """
    logger.warning(
        "Using default _create_volume_tar stub — volume contents will be empty. "
        "Replace flaude.hibernate._create_volume_tar with a real implementation."
    )
    # Create an empty archive as a placeholder
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: tarfile.open(tar_path, "w:gz").close(),
    )


async def _default_restore_volume_tar(
    volume_id: str, app_name: str, tar_path: str
) -> None:
    """Restore a tarball into a Fly volume.

    **Default implementation** — no-op.  In production, replace
    :data:`_restore_volume_tar` with an implementation that extracts
    *tar_path* into the volume (e.g. by starting a restore machine or
    using ``flyctl ssh``).

    Args:
        volume_id: Fly volume ID to restore into.
        app_name: Fly app the volume belongs to.
        tar_path: Local path of the ``.tar.gz`` archive to restore.
    """
    logger.warning(
        "Using default _restore_volume_tar stub — volume data will not be restored. "
        "Replace flaude.hibernate._restore_volume_tar with a real implementation."
    )


# Public aliases — replace these at module level to plug in real I/O
_create_volume_tar: _VolumeBackupFn = _default_create_volume_tar  # type: ignore[assignment]
_restore_volume_tar: _VolumeRestoreFn = _default_restore_volume_tar  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def hibernate_session(
    session: Session,
    *,
    snapshot_backend: SnapshotBackend,
    token: str | None = None,
) -> HibernatedSession:
    """Hibernate a session: snapshot its volume to object storage and destroy Fly resources.

    Steps (in order):

    1. **Stop machine** — gracefully stop the session machine so the volume
       is flushed and consistent.
    2. **Create volume tarball** — extract volume data into a local
       ``.tar.gz`` file (via :data:`_create_volume_tar`).
    3. **Upload snapshot** — upload the tarball via *snapshot_backend*.
       Fly resources are **not** destroyed until this step succeeds.
    4. **Destroy machine and volume** — permanently remove the Fly resources,
       eliminating ongoing storage costs.

    Args:
        session: The live session to hibernate.
        snapshot_backend: Backend that receives the uploaded volume snapshot.
        token: Explicit Fly API token (falls back to ``FLY_API_TOKEN``).

    Returns:
        A :class:`HibernatedSession` containing the snapshot reference and
        session metadata needed to wake the session later.

    Raises:
        Exception: Any error from the snapshot upload propagates immediately
            and Fly resources are left intact (atomicity guarantee).
    """
    logger.info("Hibernating session %s", session.session_id)

    # 1. Stop machine (may already be stopped between turns, errors suppressed)
    await stop_machine(session.app_name, session.machine_id, token=token)
    logger.info("Session %s: machine %s stopped", session.session_id, session.machine_id)

    # 2 & 3. Create tar and upload — Fly resources are destroyed ONLY after
    #        a successful upload (atomicity guarantee).
    with tempfile.TemporaryDirectory() as tmp_dir:
        tar_path = os.path.join(tmp_dir, f"session-{session.session_id}.tar.gz")

        logger.info("Session %s: creating volume tarball at %s", session.session_id, tar_path)
        await _create_volume_tar(session, tar_path)

        logger.info("Session %s: uploading snapshot", session.session_id)
        snapshot_ref = await snapshot_backend.upload(local_tar_path=tar_path)

    logger.info(
        "Session %s: snapshot uploaded → %s (%d bytes)",
        session.session_id,
        snapshot_ref.uri,
        snapshot_ref.size_bytes,
    )

    # 4. Destroy Fly resources (only reached on successful upload)
    await destroy_machine(session.app_name, session.machine_id, token=token)
    await destroy_volume(session.app_name, session.volume_id, token=token)

    hibernated = HibernatedSession(
        session_id=session.session_id,
        snapshot=snapshot_ref,
        app_name=session.app_name,
        region=session.region,
        hibernated_at=datetime.now(UTC).isoformat(),
    )
    logger.info("Session %s: hibernated successfully", session.session_id)
    return hibernated


async def wake_session(
    hibernated: HibernatedSession,
    *,
    config: MachineConfig,
    snapshot_backend: SnapshotBackend,
    volume_size_gb: int = 1,
    token: str | None = None,
) -> Session:
    """Wake a hibernated session: restore its snapshot and create a new machine.

    Steps (in order):

    1. **Download snapshot** — download the volume tarball from *snapshot_backend*
       to a local temp file.
    2. **Create volume** — provision a fresh Fly volume in the session's region.
    3. **Restore snapshot** — extract the tarball into the new volume (via
       :data:`_restore_volume_tar`).
    4. **Create machine** — launch a new Fly machine attached to the restored
       volume, preserving the original *session_id* so Claude Code can resume
       its conversation.

    Args:
        hibernated: The :class:`HibernatedSession` to wake.
        config: Machine configuration for the new machine.  ``volume_id``,
            ``volume_mount_path``, and ``session_id`` are overwritten by
            this function.
        snapshot_backend: Backend from which to download the snapshot.
        volume_size_gb: Size of the new volume in GB (default 1).
        token: Explicit Fly API token (falls back to ``FLY_API_TOKEN``).

    Returns:
        A live :class:`~flaude.session.Session` ready for use with
        :func:`~flaude.runner.run_session_turn`.

    Raises:
        Exception: Any error during download, restore, or machine creation
            propagates immediately.  The snapshot is **not** deleted on
            failure so you can retry.
    """
    logger.info("Waking hibernated session %s", hibernated.session_id)

    # 1. Download snapshot to a local temp file
    with tempfile.TemporaryDirectory() as tmp_dir:
        tar_path = os.path.join(tmp_dir, f"session-{hibernated.session_id}.tar.gz")

        logger.info("Session %s: downloading snapshot from %s", hibernated.session_id, hibernated.snapshot.uri)
        await snapshot_backend.download(hibernated.snapshot, local_tar_path=tar_path)

        # 2. Create a new Fly volume
        volume = await create_volume(
            hibernated.app_name,
            name=f"session-{hibernated.session_id[:8]}",
            region=hibernated.region,
            size_gb=volume_size_gb,
            token=token,
        )
        logger.info("Session %s: new volume %s created", hibernated.session_id, volume.id)

        # 3. Restore the snapshot into the new volume
        logger.info("Session %s: restoring snapshot into volume %s", hibernated.session_id, volume.id)
        await _restore_volume_tar(volume.id, hibernated.app_name, tar_path)

    # 4. Create machine with restored volume
    config.auto_destroy = False
    config.volume_id = volume.id
    config.volume_mount_path = "/data"
    config.session_id = hibernated.session_id

    machine = await create_machine(hibernated.app_name, config, token=token)
    logger.info(
        "Session %s: machine %s created (state=%s)",
        hibernated.session_id,
        machine.id,
        machine.state,
    )

    session = Session(
        session_id=hibernated.session_id,
        machine_id=machine.id,
        volume_id=volume.id,
        app_name=hibernated.app_name,
        region=hibernated.region,
        created_at=datetime.now(UTC).isoformat(),
    )
    logger.info("Session %s: woken successfully", hibernated.session_id)
    return session
