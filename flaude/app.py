"""Fly.io app lifecycle management — create or reuse an app for flaude machines."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from flaude.fly_client import FlyAPIError, fly_get, fly_post

logger = logging.getLogger(__name__)

DEFAULT_APP_PREFIX = "flaude"
DEFAULT_ORG = "personal"
DEFAULT_REGION = "iad"


@dataclass(frozen=True)
class FlyApp:
    """Represents a Fly.io application used by flaude.

    Attributes:
        name: The Fly.io application name.
        org: The Fly.io organization slug that owns the app.
        region: The preferred region for machines created under this app.
            Stored locally; not a Fly.io API-level concept for apps.
    """

    name: str
    org: str
    region: str = DEFAULT_REGION


async def get_app(app_name: str, *, token: str | None = None) -> FlyApp | None:
    """Return a FlyApp if it already exists, or None if not found.

    Args:
        app_name: The Fly.io application name to look up.
        token: Optional explicit API token (otherwise reads ``FLY_API_TOKEN``).

    Returns:
        A :class:`FlyApp` dataclass if the app exists, or ``None`` if the app
        is not found (HTTP 404).

    Raises:
        FlyAPIError: If the API returns any error other than 404.
    """
    try:
        data = await fly_get(f"/apps/{app_name}", token=token)
        if data and isinstance(data, dict):
            return FlyApp(
                name=data.get("name", app_name),
                org=data.get("organization", {}).get("slug", DEFAULT_ORG)
                if isinstance(data.get("organization"), dict)
                else DEFAULT_ORG,
            )
        return None
    except FlyAPIError as exc:
        if exc.status_code == 404:
            return None
        raise


async def create_app(
    app_name: str,
    *,
    org: str = DEFAULT_ORG,
    region: str = DEFAULT_REGION,
    token: str | None = None,
) -> FlyApp:
    """Create a new Fly.io app with configurable name, org, and region.

    The ``region`` is stored in the returned :class:`FlyApp` as the preferred
    region for machines created under this app.  Fly.io assigns machines to
    regions at machine-creation time, not at app-creation time, so this value
    is used as a convenient default rather than sent to the app-creation API.

    Args:
        app_name: Unique name for the new Fly.io application.
        org: Fly.io organization slug that will own the app.
            Defaults to ``personal``.
        region: Preferred Fly.io region for machines in this app
            (e.g. ``iad``, ``lax``, ``fra``).  Defaults to ``iad``.
        token: Optional explicit API token (otherwise reads ``FLY_API_TOKEN``).

    Returns:
        A :class:`FlyApp` dataclass with the app name, org, and region.

    Raises:
        FlyAPIError: If the API call fails (e.g. name taken, auth error).
    """
    payload = {
        "app_name": app_name,
        "org_slug": org,
    }
    logger.info("Creating Fly app %r in org %r (preferred region: %s)", app_name, org, region)
    await fly_post("/apps", json=payload, token=token)
    logger.info("Fly app %r created successfully", app_name)
    return FlyApp(name=app_name, org=org, region=region)


async def ensure_app(
    app_name: str | None = None,
    *,
    org: str = DEFAULT_ORG,
    region: str = DEFAULT_REGION,
    token: str | None = None,
) -> FlyApp:
    """Ensure a Fly.io app exists, creating it if necessary.

    Args:
        app_name: Name for the Fly app. Defaults to ``flaude``.
        org: Fly.io organization slug. Defaults to ``personal``.
        region: Preferred Fly.io region for machines created under this app.
            Defaults to ``iad``.  Only applied when a new app is created.
        token: Optional explicit API token (otherwise reads FLY_API_TOKEN).

    Returns:
        A FlyApp dataclass with the app name, org, and preferred region.
    """
    name = app_name or DEFAULT_APP_PREFIX

    existing = await get_app(name, token=token)
    if existing is not None:
        logger.info("Fly app %r already exists, reusing", name)
        # Preserve the caller's region preference even for existing apps
        if existing.region != region:
            return FlyApp(name=existing.name, org=existing.org, region=region)
        return existing

    return await create_app(name, org=org, region=region, token=token)
