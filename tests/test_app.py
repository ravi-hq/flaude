"""Tests for flaude.app — Fly.io app lifecycle management."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from flaude.app import (
    DEFAULT_APP_PREFIX,
    DEFAULT_ORG,
    DEFAULT_REGION,
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
async def test_get_app_returns_flyapp_when_exists() -> None:
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
async def test_get_app_returns_none_when_not_found() -> None:
    """get_app returns None on 404."""
    respx.get(f"{FLY_API_BASE}/apps/nope").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )
    result = await get_app("nope", token="test-token")
    assert result is None


@respx.mock
async def test_get_app_raises_on_server_error() -> None:
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
async def test_create_app_sends_correct_payload() -> None:
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
async def test_create_app_raises_on_conflict() -> None:
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
async def test_ensure_app_reuses_existing() -> None:
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
async def test_ensure_app_creates_when_missing() -> None:
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
async def test_ensure_app_uses_default_name() -> None:
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
async def test_ensure_app_custom_org() -> None:
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


# ---------------------------------------------------------------------------
# region support
# ---------------------------------------------------------------------------


def test_flyapp_has_default_region() -> None:
    """FlyApp uses DEFAULT_REGION when region is not specified."""
    app = FlyApp(name="my-app", org="personal")
    assert app.region == DEFAULT_REGION


def test_flyapp_stores_custom_region() -> None:
    """FlyApp stores a custom region."""
    app = FlyApp(name="my-app", org="personal", region="lax")
    assert app.region == "lax"


@respx.mock
async def test_create_app_custom_region() -> None:
    """create_app stores custom region in the returned FlyApp."""
    respx.post(f"{FLY_API_BASE}/apps").mock(
        return_value=httpx.Response(201, json={"name": "lax-app"})
    )
    result = await create_app(
        "lax-app", org="personal", region="lax", token="test-token"
    )
    assert result == FlyApp(name="lax-app", org="personal", region="lax")
    assert result.region == "lax"


@respx.mock
async def test_create_app_default_region() -> None:
    """create_app uses DEFAULT_REGION when region is not specified."""
    respx.post(f"{FLY_API_BASE}/apps").mock(
        return_value=httpx.Response(201, json={"name": "default-region-app"})
    )
    result = await create_app("default-region-app", token="test-token")
    assert result.region == DEFAULT_REGION


@respx.mock
async def test_create_app_does_not_send_region_in_payload() -> None:
    """create_app does not send region in the payload (Fly manages regions at machine
    level)."""
    route = respx.post(f"{FLY_API_BASE}/apps").mock(
        return_value=httpx.Response(201, json={"name": "region-test"})
    )
    await create_app("region-test", region="fra", token="test-token")
    body = json.loads(route.calls.last.request.content)
    # region is NOT sent to the app creation API — it's a machine-level concept
    assert "region" not in body
    assert body == {"app_name": "region-test", "org_slug": DEFAULT_ORG}


@respx.mock
async def test_ensure_app_custom_region_on_create() -> None:
    """ensure_app passes custom region when creating a new app."""
    respx.get(f"{FLY_API_BASE}/apps/fra-app").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )
    respx.post(f"{FLY_API_BASE}/apps").mock(
        return_value=httpx.Response(201, json={"name": "fra-app"})
    )
    result = await ensure_app("fra-app", region="fra", token="test-token")
    assert result == FlyApp(name="fra-app", org=DEFAULT_ORG, region="fra")
    assert result.region == "fra"


@respx.mock
async def test_ensure_app_region_applied_to_existing() -> None:
    """ensure_app applies caller's region preference to an existing app."""
    respx.get(f"{FLY_API_BASE}/apps/my-app").mock(
        return_value=httpx.Response(
            200,
            json={"name": "my-app", "organization": {"slug": "personal"}},
        )
    )
    # The app exists; caller wants lax region preference
    result = await ensure_app("my-app", region="lax", token="test-token")
    assert result.region == "lax"
    assert result.name == "my-app"
