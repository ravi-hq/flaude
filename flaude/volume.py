"""Fly.io volume lifecycle — create, list, and destroy volumes."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from flaude.fly_client import FlyAPIError, fly_delete, fly_get, fly_post

logger = logging.getLogger(__name__)

DEFAULT_VOLUME_SIZE_GB = 1


@dataclass(frozen=True)
class FlyVolume:
    """Represents a Fly.io volume.

    Attributes:
        id: The unique Fly volume ID.
        name: Human-readable volume name.
        region: Fly.io region the volume is in.
        size_gb: Volume size in gigabytes.
        app_name: The Fly app this volume belongs to.
        state: Current volume state.
    """

    id: str
    name: str
    region: str
    size_gb: int
    app_name: str
    state: str


def _parse_volume_response(data: dict, app_name: str) -> FlyVolume:
    """Parse a Fly Volumes API response into a FlyVolume."""
    return FlyVolume(
        id=data["id"],
        name=data.get("name", ""),
        region=data.get("region", ""),
        size_gb=data.get("size_gb", 0),
        app_name=app_name,
        state=data.get("state", "unknown"),
    )


async def create_volume(
    app_name: str,
    *,
    name: str = "flaude_session",
    region: str = "iad",
    size_gb: int = DEFAULT_VOLUME_SIZE_GB,
    token: str | None = None,
) -> FlyVolume:
    """Create a Fly.io volume for session persistence.

    Args:
        app_name: The Fly app to create the volume under.
        name: Volume name (visible in Fly dashboard).
        region: Region for the volume (must match machine region).
        size_gb: Volume size in GB (default 1).
        token: Explicit API token.

    Returns:
        A :class:`FlyVolume` with the volume's ID and metadata.
    """
    payload = {
        "name": name,
        "region": region,
        "size_gb": size_gb,
    }

    logger.info(
        "Creating volume in app %r region=%s size=%dGB",
        app_name,
        region,
        size_gb,
    )

    data = await fly_post(
        f"/apps/{app_name}/volumes",
        json=payload,
        token=token,
    )

    if not data or not isinstance(data, dict):
        raise FlyAPIError(
            status_code=0,
            detail="Empty or invalid response from create-volume endpoint",
            method="POST",
            url=f"/apps/{app_name}/volumes",
        )

    volume = _parse_volume_response(data, app_name)
    logger.info(
        "Volume %s created (region=%s, size=%dGB)",
        volume.id,
        volume.region,
        volume.size_gb,
    )
    return volume


async def list_volumes(
    app_name: str,
    *,
    token: str | None = None,
) -> list[FlyVolume]:
    """List all volumes for a Fly app.

    Args:
        app_name: The Fly app to list volumes for.
        token: Explicit API token.

    Returns:
        List of :class:`FlyVolume` objects.
    """
    data = await fly_get(
        f"/apps/{app_name}/volumes",
        token=token,
    )

    if not data or not isinstance(data, list):
        return []

    return [_parse_volume_response(v, app_name) for v in data]


async def destroy_volume(
    app_name: str,
    volume_id: str,
    *,
    token: str | None = None,
) -> None:
    """Destroy a Fly.io volume permanently.

    Args:
        app_name: The Fly app the volume belongs to.
        volume_id: The volume ID to destroy.
        token: Explicit API token.
    """
    try:
        await fly_delete(
            f"/apps/{app_name}/volumes/{volume_id}",
            token=token,
        )
        logger.info("Volume %s destroyed", volume_id)
    except FlyAPIError as exc:
        if exc.status_code == 404:
            logger.debug("Volume %s already gone (404)", volume_id)
        else:
            raise
