"""Serverless session management — persistent multi-turn Claude Code sessions.

A session maps to a Fly machine + volume pair. The machine is stopped
between prompts and restarted on demand. Claude Code's conversation
state persists on the Fly Volume via ``CLAUDE_CONFIG_DIR``.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flaude.runner import RunResult

from flaude.machine import create_machine, destroy_machine
from flaude.machine_config import MachineConfig
from flaude.volume import create_volume, destroy_volume

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
        elapsed = (datetime.now(UTC) - created).total_seconds()
        return elapsed > self.ttl_seconds


async def create_session(
    app_name: str,
    config: MachineConfig,
    *,
    name: str | None = None,
    volume_size_gb: int = 1,
    ttl_seconds: int = 0,
    token: str | None = None,
) -> tuple[Session, RunResult]:
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
    from flaude.runner import RunResult, wait_for_machine_exit  # noqa: PLC0415

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
        created_at=datetime.now(UTC).isoformat(),
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
