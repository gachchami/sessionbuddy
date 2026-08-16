from __future__ import annotations

import hashlib

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from scripts.embed_console_assets import ASSETS, CONTENT_ADDRESSED_ASSETS, STATIC
from sessionbuddy.cfp.router import cfp_router
from sessionbuddy.competition.router import competition_router
from sessionbuddy.console import embedded_assets
from sessionbuddy.console.asset_response import content_addressed_asset
from sessionbuddy.evaluation.router import evaluation_router
from sessionbuddy.platform.auth.access import access_router
from sessionbuddy.scheduling.router import scheduling_router
from sessionbuddy.speaker_operations.router import speaker_operations_router


def _request(query: str = "") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/asset.js",
            "query_string": query.encode("ascii"),
            "headers": [],
        }
    )


def test_matching_content_identity_is_immutable() -> None:
    content = "console.log('versioned');"
    digest = hashlib.sha256(content.encode()).hexdigest()[:12]

    response = content_addressed_asset(
        _request(f"v={digest}"), content, media_type="text/javascript"
    )

    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_missing_or_stale_content_identity_is_not_cached() -> None:
    for query in ("", "v=stale"):
        response = content_addressed_asset(_request(query), "body", media_type="text/javascript")
        assert response.headers["cache-control"] == "no-store"


def test_disk_and_embedded_content_addresses_are_identical() -> None:
    for _, asset_name, _ in CONTENT_ADDRESSED_ASSETS:
        disk = (STATIC / asset_name).read_bytes()
        served = getattr(embedded_assets, ASSETS[asset_name]).encode("utf-8")
        assert served == disk, asset_name
        assert hashlib.sha256(served).hexdigest()[:12] == hashlib.sha256(disk).hexdigest()[
            :12
        ]


@pytest.mark.asyncio
async def test_every_content_addressed_route_enforces_its_identity() -> None:
    app = FastAPI()
    for router in (
        access_router,
        cfp_router,
        competition_router,
        evaluation_router,
        scheduling_router,
        speaker_operations_router,
    ):
        app.include_router(router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        for _, asset_name, asset_path in CONTENT_ADDRESSED_ASSETS:
            content = getattr(embedded_assets, ASSETS[asset_name]).encode("utf-8")
            digest = hashlib.sha256(content).hexdigest()[:12]
            versioned = await client.get(f"{asset_path}?v={digest}")
            unversioned = await client.get(asset_path)
            assert versioned.status_code == 200, asset_path
            assert versioned.headers["cache-control"] == (
                "public, max-age=31536000, immutable"
            )
            assert unversioned.status_code == 200, asset_path
            assert unversioned.headers["cache-control"] == "no-store"
