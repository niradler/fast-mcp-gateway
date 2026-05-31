"""Tests for factory.build_app: full in-process ASGI round-trips via asgi-lifespan."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from fast_gateway.config import GatewayConfig, HilConfig
from fast_gateway.factory import build_app
from fast_gateway.models import ServerAuth


def _make_config(tmp_path: Path, **overrides: object) -> GatewayConfig:
    defaults: dict[str, object] = {"db": str(tmp_path / "test.db"), "hil": HilConfig(enabled=False)}
    defaults.update(overrides)
    return GatewayConfig.model_validate(defaults)


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    cfg = _make_config(tmp_path)
    application = build_app(cfg)
    async with LifespanManager(application) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_build_app_returns_fastapi(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    application = build_app(cfg)
    assert isinstance(application, FastAPI)


async def test_create_and_list_server(client: httpx.AsyncClient) -> None:
    payload = {"name": "weather", "url": "https://example.com/mcp", "transport": "http"}
    r = await client.post("/admin/servers", json=payload)
    assert r.status_code == 201
    server = r.json()
    assert server["name"] == "weather"

    r2 = await client.get("/admin/servers")
    assert r2.status_code == 200
    assert any(s["name"] == "weather" for s in r2.json())


async def test_reload_returns_ok(client: httpx.AsyncClient) -> None:
    r = await client.post("/admin/reload")
    assert r.status_code == 200
    assert r.json()["status"] == "reloaded"


async def test_duplicate_server_is_409(client: httpx.AsyncClient) -> None:
    payload = {"name": "myserver", "url": "https://example.com/mcp", "transport": "http"}
    await client.post("/admin/servers", json=payload)
    r2 = await client.post("/admin/servers", json=payload)
    assert r2.status_code == 409


async def test_admin_token_missing_bearer_is_401(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path, admin_token="supersecret")
    application = build_app(cfg)
    async with LifespanManager(application) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/admin/servers")
            assert r.status_code == 401


async def test_admin_token_correct_bearer_passes(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path, admin_token="supersecret")
    application = build_app(cfg)
    async with LifespanManager(application) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": "Bearer supersecret"},
        ) as c:
            r = await c.get("/admin/servers")
            assert r.status_code == 200


async def test_no_admin_token_allows_unauthenticated(client: httpx.AsyncClient) -> None:
    r = await client.get("/admin/servers")
    assert r.status_code == 200


async def test_hil_plugin_mounts_admin_route(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path, hil=HilConfig(enabled=True, auto_open_browser=False))
    application = build_app(cfg)
    async with LifespanManager(application) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/admin/hil")
            assert r.status_code == 200


async def test_oauth_plugin_wired_in_build_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAST_GATEWAY_OAUTH_DIR", str(tmp_path / "tokens"))
    cfg = _make_config(tmp_path)
    application = build_app(cfg)
    async with LifespanManager(application) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            payload = {
                "name": "oauth-srv",
                "url": "https://example.com/mcp",
                "transport": "http",
                "auth": ServerAuth.OAUTH,
            }
            r = await c.post("/admin/servers", json=payload)
            assert r.status_code == 201
            server = r.json()
            assert server["auth"] == "oauth"
