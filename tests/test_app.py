"""Tests for flaude.app — Fly.io app lifecycle management."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from flaude.app import (
    DEFAULT_APP_PREFIX,
    DEFAULT_ORG,
    FlyApp,
    create_app,
    ensure_app,
    get_app,
)
from flaude.fly_client import FLY_API_BASE, FlyAPIError


# ---------------------------------------------------------------------------
# get_app
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_app_returns_flyapp_when_exists():
    """get_app returns a FlyApp when the API returns 200."""
    respx.get(f"{FLY_API_BASE}/apps/my-app").mock(
        return_value=httpx.Response(
            200,
            json={"name": "my-app", "organization": {"slug": "myorg"}},
        )
    )
    result = await get_app("my-app", token="test-token")
    assert result == FlyApp(name="my-app", org="myorg")


@respx.mock
async def test_get_app_returns_none_when_not_found():
    """get_app returns None on 404."""
    respx.get(f"{FLY_API_BASE}/apps/nope").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )
    result = await get_app("nope", token="test-token")
    assert result is None


@respx.mock
async def test_get_app_raises_on_server_error():
    """get_app raises FlyAPIError on 5xx."""
    respx.get(f"{FLY_API_BASE}/apps/boom").mock(
        return_value=httpx.Response(500, text="internal error")
    )
    with pytest.raises(FlyAPIError) as exc_info:
        await get_app("boom", token="test-token")
    assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# create_app
# ---------------------------------------------------------------------------


@respx.mock
async def test_create_app_sends_correct_payload():
    """create_app POSTs with app_name and org_slug."""
    route = respx.post(f"{FLY_API_BASE}/apps").mock(
        return_value=httpx.Response(201, json={"name": "new-app"})
    )
    result = await create_app("new-app", org="myorg", token="test-token")

    assert result == FlyApp(name="new-app", org="myorg")
    # Verify the payload sent
    request = route.calls.last.request
    body = json.loads(request.content)
    assert body == {"app_name": "new-app", "org_slug": "myorg"}


@respx.mock
async def test_create_app_raises_on_conflict():
    """create_app raises FlyAPIError when app name is taken."""
    respx.post(f"{FLY_API_BASE}/apps").mock(
        return_value=httpx.Response(422, text="name already taken")
    )
    with pytest.raises(FlyAPIError) as exc_info:
        await create_app("taken-app", token="test-token")
    assert exc_info.value.status_code == 422


# ---------------------------------------------------------------------------
# ensure_app
# ---------------------------------------------------------------------------


@respx.mock
async def test_ensure_app_reuses_existing():
    """ensure_app returns existing app without creating."""
    respx.get(f"{FLY_API_BASE}/apps/my-app").mock(
        return_value=httpx.Response(
            200,
            json={"name": "my-app", "organization": {"slug": "personal"}},
        )
    )
    # Should NOT call POST
    result = await ensure_app("my-app", token="test-token")
    assert result == FlyApp(name="my-app", org="personal")


@respx.mock
async def test_ensure_app_creates_when_missing():
    """ensure_app creates app when it doesn't exist."""
    respx.get(f"{FLY_API_BASE}/apps/fresh-app").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )
    respx.post(f"{FLY_API_BASE}/apps").mock(
        return_value=httpx.Response(201, json={"name": "fresh-app"})
    )
    result = await ensure_app("fresh-app", token="test-token")
    assert result == FlyApp(name="fresh-app", org=DEFAULT_ORG)


@respx.mock
async def test_ensure_app_uses_default_name():
    """ensure_app defaults to the flaude app name prefix."""
    respx.get(f"{FLY_API_BASE}/apps/{DEFAULT_APP_PREFIX}").mock(
        return_value=httpx.Response(
            200,
            json={
                "name": DEFAULT_APP_PREFIX,
                "organization": {"slug": "personal"},
            },
        )
    )
    result = await ensure_app(token="test-token")
    assert result.name == DEFAULT_APP_PREFIX


@respx.mock
async def test_ensure_app_custom_org():
    """ensure_app passes custom org to create_app."""
    respx.get(f"{FLY_API_BASE}/apps/org-app").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )
    route = respx.post(f"{FLY_API_BASE}/apps").mock(
        return_value=httpx.Response(201, json={"name": "org-app"})
    )
    result = await ensure_app("org-app", org="my-org", token="test-token")
    assert result == FlyApp(name="org-app", org="my-org")
    body = json.loads(route.calls.last.request.content)
    assert body["org_slug"] == "my-org"
