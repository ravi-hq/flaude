"""Low-level async HTTP client for the Fly.io Machines API."""

from __future__ import annotations

import os
from typing import Any

import httpx

FLY_API_BASE = "https://api.machines.dev/v1"


class FlyAPIError(Exception):
    """Raised when a Fly.io API call fails."""

    def __init__(self, status_code: int, detail: str, method: str = "", url: str = ""):
        self.status_code = status_code
        self.detail = detail
        self.method = method
        self.url = url
        super().__init__(
            f"Fly API error {status_code} {method} {url}: {detail}"
        )


def _get_token() -> str:
    token = os.environ.get("FLY_API_TOKEN", "")
    if not token:
        raise EnvironmentError(
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

    return response.json()


async def fly_get(path: str, **kwargs: Any) -> Any:
    return await fly_request("GET", path, **kwargs)


async def fly_post(path: str, **kwargs: Any) -> Any:
    return await fly_request("POST", path, **kwargs)


async def fly_delete(path: str, **kwargs: Any) -> Any:
    return await fly_request("DELETE", path, **kwargs)
