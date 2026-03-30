"""Fly.io machine lifecycle — create, wait, stop, and destroy machines."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from flaude.fly_client import FlyAPIError, fly_delete, fly_get, fly_post, fly_put
from flaude.machine_config import MachineConfig, build_machine_config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FlyMachine:
    """Represents a running (or recently created) Fly.io machine.

    Attributes:
        id: The unique Fly machine ID assigned by the Fly Machines API.
        name: Human-readable name for the machine (may be empty).
        state: Current machine state (e.g. ``created``, ``started``, ``stopped``).
        region: The Fly.io region the machine is running in (e.g. ``iad``).
        instance_id: Internal instance identifier assigned by Fly.io.
        app_name: The Fly.io application this machine belongs to.
    """

    id: str
    name: str
    state: str
    region: str
    instance_id: str
    app_name: str

    async def cleanup(self, *, token: str | None = None) -> None:
        """Stop and destroy this machine, handling all edge cases gracefully.

        This method first attempts to stop the machine, then destroys it.
        Both steps tolerate already-stopped and already-destroyed states,
        ensuring no orphaned resources remain.

        Args:
            token: Explicit API token (falls back to ``FLY_API_TOKEN``).
        """
        logger.info("Cleaning up machine %s in app %s", self.id, self.app_name)
        await stop_machine(self.app_name, self.id, token=token)
        await destroy_machine(self.app_name, self.id, token=token)


def _parse_machine_response(data: dict[str, Any], app_name: str) -> FlyMachine:
    """Parse a Fly Machines API response into a FlyMachine."""
    return FlyMachine(
        id=data["id"],
        name=data.get("name", ""),
        state=data.get("state", "unknown"),
        region=data.get("region", ""),
        instance_id=data.get("instance_id", ""),
        app_name=app_name,
    )


async def create_machine(
    app_name: str,
    config: MachineConfig,
    *,
    name: str | None = None,
    token: str | None = None,
    timeout: float = 60.0,
) -> FlyMachine:
    """Create a Fly.io machine and return its ID/status.

    Sends a POST to ``/v1/apps/{app}/machines`` with the payload built from
    *config*.  The Fly API returns the machine details synchronously once the
    machine has been accepted (not necessarily started).

    Args:
        app_name: The Fly app to create the machine under.
        config: A :class:`MachineConfig` describing the desired machine.
        name: Optional human-readable name for the machine.
        token: Explicit API token (falls back to ``FLY_API_TOKEN``).
        timeout: HTTP request timeout in seconds.

    Returns:
        A :class:`FlyMachine` with the machine's ID, state, region, etc.

    Raises:
        ValueError: If required config fields are missing.
        FlyAPIError: If the Fly API returns an error.
    """
    payload = build_machine_config(config)

    if name:
        payload["name"] = name

    logger.info(
        "Creating machine in app %r region=%s image=%s",
        app_name,
        config.region,
        config.image,
    )

    data = await fly_post(
        f"/apps/{app_name}/machines",
        json=payload,
        token=token,
        timeout=timeout,
    )

    if not data or not isinstance(data, dict):
        raise FlyAPIError(
            status_code=0,
            detail="Empty or invalid response from create-machine endpoint",
            method="POST",
            url=f"/apps/{app_name}/machines",
        )

    machine = _parse_machine_response(data, app_name)
    logger.info(
        "Machine %s created (state=%s, region=%s)",
        machine.id,
        machine.state,
        machine.region,
    )
    return machine


async def get_machine(
    app_name: str,
    machine_id: str,
    *,
    token: str | None = None,
) -> FlyMachine:
    """Fetch the current state of a machine.

    Args:
        app_name: The Fly app the machine belongs to.
        machine_id: The machine ID.
        token: Explicit API token.

    Returns:
        A :class:`FlyMachine` with updated state.
    """
    data = await fly_get(
        f"/apps/{app_name}/machines/{machine_id}",
        token=token,
    )
    if not data or not isinstance(data, dict):
        raise FlyAPIError(
            status_code=0,
            detail="Empty or invalid response from get-machine endpoint",
            method="GET",
            url=f"/apps/{app_name}/machines/{machine_id}",
        )
    return _parse_machine_response(data, app_name)


async def stop_machine(
    app_name: str,
    machine_id: str,
    *,
    token: str | None = None,
) -> None:
    """Send a stop signal to a machine.

    This is a best-effort call — if the machine is already stopped or
    destroyed the error is suppressed.

    Args:
        app_name: The Fly app the machine belongs to.
        machine_id: The machine ID to stop.
        token: Explicit API token (falls back to ``FLY_API_TOKEN``).
    """
    try:
        await fly_post(
            f"/apps/{app_name}/machines/{machine_id}/stop",
            token=token,
        )
        logger.info("Stop signal sent to machine %s", machine_id)
    except FlyAPIError as exc:
        # 404 = already gone, 409 = already stopped / not in stoppable state
        if exc.status_code in (404, 409):
            logger.debug(
                "Machine %s stop returned %s (already stopped/gone)",
                machine_id,
                exc.status_code,
            )
        else:
            raise


async def destroy_machine(
    app_name: str,
    machine_id: str,
    *,
    force: bool = True,
    token: str | None = None,
) -> None:
    """Destroy a machine, removing it permanently.

    Args:
        app_name: The Fly app the machine belongs to.
        machine_id: The machine ID to destroy.
        force: If True, force-destroy even if the machine is running.
        token: Explicit API token.
    """
    path = f"/apps/{app_name}/machines/{machine_id}"
    if force:
        path += "?force=true"

    try:
        await fly_delete(path, token=token)
        logger.info("Machine %s destroyed", machine_id)
    except FlyAPIError as exc:
        if exc.status_code == 404:
            logger.debug("Machine %s already gone (404)", machine_id)
        else:
            raise


async def start_machine(
    app_name: str,
    machine_id: str,
    *,
    token: str | None = None,
) -> None:
    """Start a stopped machine.

    Best-effort — if the machine is already started or gone, the error
    is suppressed.

    Args:
        app_name: The Fly app the machine belongs to.
        machine_id: The machine ID to start.
        token: Explicit API token (falls back to ``FLY_API_TOKEN``).
    """
    try:
        await fly_post(
            f"/apps/{app_name}/machines/{machine_id}/start",
            token=token,
        )
        logger.info("Start signal sent to machine %s", machine_id)
    except FlyAPIError as exc:
        if exc.status_code in (404, 409):
            logger.debug(
                "Machine %s start returned %s (already started/gone)",
                machine_id,
                exc.status_code,
            )
        else:
            raise


async def update_machine(
    app_name: str,
    machine_id: str,
    config: MachineConfig,
    *,
    name: str | None = None,
    token: str | None = None,
    timeout: float = 60.0,
) -> FlyMachine:
    """Update a stopped machine's configuration.

    Sends a PUT to ``/v1/apps/{app}/machines/{id}`` with the full config
    payload. Used to inject new env vars (prompt, session ID) between
    session turns.

    Args:
        app_name: The Fly app the machine belongs to.
        machine_id: The machine ID to update.
        config: Updated :class:`MachineConfig`.
        name: Optional machine name override.
        token: Explicit API token.
        timeout: HTTP request timeout in seconds.

    Returns:
        A :class:`FlyMachine` with updated state.
    """
    payload = build_machine_config(config)
    if name:
        payload["name"] = name

    logger.info("Updating machine %s in app %r", machine_id, app_name)

    data = await fly_put(
        f"/apps/{app_name}/machines/{machine_id}",
        json=payload,
        token=token,
        timeout=timeout,
    )

    if not data or not isinstance(data, dict):
        raise FlyAPIError(
            status_code=0,
            detail="Empty or invalid response from update-machine endpoint",
            method="PUT",
            url=f"/apps/{app_name}/machines/{machine_id}",
        )

    machine = _parse_machine_response(data, app_name)
    logger.info("Machine %s updated (state=%s)", machine.id, machine.state)
    return machine
