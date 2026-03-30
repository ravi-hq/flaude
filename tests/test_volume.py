"""Tests for flaude.volume — Fly volume lifecycle operations."""

from __future__ import annotations

import httpx
import pytest
import respx

from flaude.fly_client import FLY_API_BASE, FlyAPIError
from flaude.volume import FlyVolume, create_volume, destroy_volume, list_volumes

APP = "flaude-test"
TOKEN = "test-fly-token"


def _volume_response(
    *,
    volume_id: str = "vol_abc123",
    name: str = "flaude_session",
    region: str = "iad",
    size_gb: int = 1,
    state: str = "created",
) -> dict:
    return {
        "id": volume_id,
        "name": name,
        "region": region,
        "size_gb": size_gb,
        "state": state,
    }


# ---------------------------------------------------------------------------
# create_volume
# ---------------------------------------------------------------------------


@respx.mock
async def test_create_volume_returns_fly_volume() -> None:
    """create_volume POSTs to the API and returns a FlyVolume."""
    route = respx.post(f"{FLY_API_BASE}/apps/{APP}/volumes").mock(
        return_value=httpx.Response(200, json=_volume_response())
    )

    volume = await create_volume(APP, token=TOKEN)

    assert isinstance(volume, FlyVolume)
    assert volume.id == "vol_abc123"
    assert volume.name == "flaude_session"
    assert volume.region == "iad"
    assert volume.size_gb == 1
    assert volume.state == "created"
    assert volume.app_name == APP
    assert route.called


@respx.mock
async def test_create_volume_sends_correct_payload() -> None:
    """create_volume POSTs the name, region, and size_gb."""
    route = respx.post(f"{FLY_API_BASE}/apps/{APP}/volumes").mock(
        return_value=httpx.Response(200, json=_volume_response(region="lhr", size_gb=5))
    )

    await create_volume(APP, name="my-vol", region="lhr", size_gb=5, token=TOKEN)

    import json

    body = json.loads(route.calls[0].request.content)
    assert body["name"] == "my-vol"
    assert body["region"] == "lhr"
    assert body["size_gb"] == 5


@respx.mock
async def test_create_volume_raises_on_api_error() -> None:
    """FlyAPIError raised when the API returns a non-2xx status."""
    respx.post(f"{FLY_API_BASE}/apps/{APP}/volumes").mock(
        return_value=httpx.Response(422, text="invalid region")
    )

    with pytest.raises(FlyAPIError) as exc_info:
        await create_volume(APP, token=TOKEN)
    assert exc_info.value.status_code == 422


@respx.mock
async def test_create_volume_raises_on_empty_response() -> None:
    """FlyAPIError raised when API returns an empty body."""
    respx.post(f"{FLY_API_BASE}/apps/{APP}/volumes").mock(
        return_value=httpx.Response(204)
    )

    with pytest.raises(FlyAPIError) as exc_info:
        await create_volume(APP, token=TOKEN)
    assert exc_info.value.status_code == 0


# ---------------------------------------------------------------------------
# list_volumes
# ---------------------------------------------------------------------------


@respx.mock
async def test_list_volumes_returns_all_volumes() -> None:
    """list_volumes GETs the volumes endpoint and returns all volumes."""
    respx.get(f"{FLY_API_BASE}/apps/{APP}/volumes").mock(
        return_value=httpx.Response(
            200,
            json=[
                _volume_response(volume_id="vol_1", name="vol-one"),
                _volume_response(volume_id="vol_2", name="vol-two"),
            ],
        )
    )

    volumes = await list_volumes(APP, token=TOKEN)

    assert len(volumes) == 2
    assert volumes[0].id == "vol_1"
    assert volumes[0].name == "vol-one"
    assert volumes[1].id == "vol_2"
    assert volumes[1].name == "vol-two"
    for v in volumes:
        assert v.app_name == APP


@respx.mock
async def test_list_volumes_returns_empty_on_no_volumes() -> None:
    """list_volumes returns an empty list when there are no volumes."""
    respx.get(f"{FLY_API_BASE}/apps/{APP}/volumes").mock(
        return_value=httpx.Response(200, json=[])
    )

    volumes = await list_volumes(APP, token=TOKEN)
    assert volumes == []


@respx.mock
async def test_list_volumes_returns_empty_on_null_response() -> None:
    """list_volumes returns an empty list when the API returns null/empty."""
    respx.get(f"{FLY_API_BASE}/apps/{APP}/volumes").mock(
        return_value=httpx.Response(204)
    )

    volumes = await list_volumes(APP, token=TOKEN)
    assert volumes == []


# ---------------------------------------------------------------------------
# destroy_volume
# ---------------------------------------------------------------------------


@respx.mock
async def test_destroy_volume_sends_delete() -> None:
    """destroy_volume sends DELETE to the volume endpoint."""
    route = respx.delete(f"{FLY_API_BASE}/apps/{APP}/volumes/vol_abc123").mock(
        return_value=httpx.Response(200, json={})
    )

    await destroy_volume(APP, "vol_abc123", token=TOKEN)
    assert route.called


@respx.mock
async def test_destroy_volume_ignores_404() -> None:
    """destroy_volume silently ignores 404 (volume already gone)."""
    respx.delete(f"{FLY_API_BASE}/apps/{APP}/volumes/vol_gone").mock(
        return_value=httpx.Response(404, text="not found")
    )

    await destroy_volume(APP, "vol_gone", token=TOKEN)


@respx.mock
async def test_destroy_volume_raises_on_other_errors() -> None:
    """destroy_volume re-raises non-404 errors."""
    respx.delete(f"{FLY_API_BASE}/apps/{APP}/volumes/vol_abc123").mock(
        return_value=httpx.Response(500, text="server error")
    )

    with pytest.raises(FlyAPIError) as exc_info:
        await destroy_volume(APP, "vol_abc123", token=TOKEN)
    assert exc_info.value.status_code == 500
