"""Low-level async HTTP client for the Fly.io Machines API."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

FLY_API_BASE = "https://api.machines.dev/v1"

# Fly platform API (separate from the Machines API) — used for logs
FLY_PLATFORM_API_BASE = "https://api.fly.io"


class FlyAPIError(Exception):
    """Raised when a Fly.io API call fails."""

    def __init__(self, status_code: int, detail: str, method: str = "", url: str = ""):
        self.status_code = status_code
        self.detail = detail
        self.method = method
        self.url = url
        super().__init__(f"Fly API error {status_code} {method} {url}: {detail}")


def _get_token() -> str:
    token = os.environ.get("FLY_API_TOKEN", "")
    if not token:
        raise OSError(
            "FLY_API_TOKEN environment variable is required for Fly.io API access"
        )
    return token


def _headers(token: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token or _get_token()}",
        "Content-Type": "application/json",
    }


async def fly_request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    json: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any] | list[Any] | None:
    """Make an authenticated request to the Fly.io Machines API.

    Returns the parsed JSON response body, or None for 204/empty responses.
    Raises FlyAPIError on non-2xx status codes.
    """
    url = f"{FLY_API_BASE}{path}"
    headers = _headers(token)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.request(method, url, headers=headers, json=json)

    if response.status_code >= 400:
        try:
            detail = response.text
        except Exception:
            detail = f"HTTP {response.status_code}"
        raise FlyAPIError(response.status_code, detail, method, url)

    if response.status_code == 204 or not response.content:
        return None

    result: dict[str, Any] | list[Any] = response.json()
    return result


async def fly_get(path: str, **kwargs: Any) -> Any:
    return await fly_request("GET", path, **kwargs)


async def fly_post(path: str, **kwargs: Any) -> Any:
    return await fly_request("POST", path, **kwargs)


async def fly_delete(path: str, **kwargs: Any) -> Any:
    return await fly_request("DELETE", path, **kwargs)


async def fetch_machine_logs(
    app_name: str,
    machine_id: str,
    *,
    token: str | None = None,
    timeout: float = 30.0,
) -> list[str]:
    """Fetch historical logs for a machine from the Fly platform logs API.

    Uses ``GET https://api.fly.io/api/v1/apps/{app}/logs`` which provides
    access to retained logs (~15 days). This works even after the machine
    has stopped or been destroyed.

    Note: The platform API (``api.fly.io``) uses a different auth format
    than the Machines API — the token is sent directly as the Authorization
    header value (e.g. ``FlyV1 ...``), not as ``Bearer <token>``.

    Args:
        app_name: The Fly app the machine belongs to.
        machine_id: The machine ID to fetch logs for.
        token: Explicit Fly API token.
        timeout: HTTP request timeout in seconds.

    Returns:
        List of log message strings from the machine.
    """
    url = f"{FLY_PLATFORM_API_BASE}/api/v1/apps/{app_name}/logs"
    # Platform API uses the raw token as Authorization (not Bearer)
    raw_token = token or _get_token()
    headers = {
        "Authorization": raw_token,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(
            url,
            headers=headers,
            params={"instance": machine_id},
        )

    if response.status_code >= 400:
        raise FlyAPIError(response.status_code, response.text, "GET", url)

    data = response.json()
    entries = data.get("data", [])

    lines: list[str] = []
    for entry in entries:
        attrs = entry.get("attributes", {})
        message = attrs.get("message", "")
        instance = attrs.get("instance", "")
        if instance == machine_id and message:
            lines.append(message)

    logger.info("Fetched %d log lines for machine %s", len(lines), machine_id)
    return lines
