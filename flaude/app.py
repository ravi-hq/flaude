"""Fly.io app lifecycle management — create or reuse an app for flaude machines."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from flaude.fly_client import FlyAPIError, fly_get, fly_post

logger = logging.getLogger(__name__)

DEFAULT_APP_PREFIX = "flaude"
DEFAULT_ORG = "personal"


@dataclass(frozen=True)
class FlyApp:
    """Represents a Fly.io application used by flaude."""

    name: str
    org: str


async def get_app(app_name: str, *, token: str | None = None) -> FlyApp | None:
    """Return a FlyApp if it already exists, or None if not found."""
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
    token: str | None = None,
) -> FlyApp:
    """Create a new Fly.io app.

    Raises FlyAPIError if the API call fails (e.g. name taken, auth error).
    """
    payload = {
        "app_name": app_name,
        "org_slug": org,
    }
    logger.info("Creating Fly app %r in org %r", app_name, org)
    await fly_post("/apps", json=payload, token=token)
    logger.info("Fly app %r created successfully", app_name)
    return FlyApp(name=app_name, org=org)


async def ensure_app(
    app_name: str | None = None,
    *,
    org: str = DEFAULT_ORG,
    token: str | None = None,
) -> FlyApp:
    """Ensure a Fly.io app exists, creating it if necessary.

    Args:
        app_name: Name for the Fly app. Defaults to ``flaude``.
        org: Fly.io organization slug. Defaults to ``personal``.
        token: Optional explicit API token (otherwise reads FLY_API_TOKEN).

    Returns:
        A FlyApp dataclass with the app name and org.
    """
    name = app_name or DEFAULT_APP_PREFIX

    existing = await get_app(name, token=token)
    if existing is not None:
        logger.info("Fly app %r already exists, reusing", name)
        return existing

    return await create_app(name, org=org, token=token)
