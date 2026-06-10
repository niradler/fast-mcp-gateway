"""Smoke tests for the scaffold: the package imports, a gateway assembles, and the
admin routes are wired (returning 501 until the handlers are implemented)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import fast_gateway
from fast_gateway import Gateway, Hooks, SqliteStore, create_gateway


def make_app() -> tuple[FastAPI, Gateway]:
    gateway = create_gateway(SqliteStore(":memory:"), Hooks())
    app = FastAPI(lifespan=gateway.lifespan)
    gateway.install(app)
    return app, gateway


def test_version_is_exposed() -> None:
    assert isinstance(fast_gateway.__version__, str)
    assert fast_gateway.__version__


def test_create_gateway_returns_gateway() -> None:
    gateway = create_gateway(SqliteStore(":memory:"))
    assert isinstance(gateway, Gateway)
    assert gateway.mcp is not None
    assert gateway.admin_router is not None


def test_admin_routes_are_registered() -> None:
    app, _ = make_app()
    paths = set(app.openapi()["paths"])
    assert "/admin/servers" in paths
    assert "/admin/groups" in paths
    assert "/admin/reload" in paths


def test_list_servers_starts_empty() -> None:
    app, _ = make_app()
    with TestClient(app) as client:
        response = client.get("/admin/servers")
    assert response.status_code == 200
    assert response.json() == []


async def test_lifespan_closes_store_on_shutdown() -> None:
    """The gateway lifespan opens the store on startup and closes it on shutdown.

    After shutdown the connection is released, so a store call raises ``RuntimeError``.
    """
    from asgi_lifespan import LifespanManager

    store = SqliteStore(":memory:")
    gateway = create_gateway(store)
    app = FastAPI(lifespan=gateway.lifespan)
    gateway.install(app)

    async with LifespanManager(app):
        assert await store.list_servers() == []

    with pytest.raises(RuntimeError):
        await store.list_servers()


# ---------------------------------------------------------------------------
# startup_catalog modes
# ---------------------------------------------------------------------------


def _make_lifespan_app(gateway: Gateway) -> FastAPI:
    app = FastAPI(lifespan=gateway.lifespan)
    gateway.install(app)
    return app


async def test_startup_catalog_skip_never_introspects() -> None:
    from unittest.mock import AsyncMock, patch

    from asgi_lifespan import LifespanManager

    from fast_gateway.models import ServerCreate

    store = SqliteStore(":memory:")
    await store.initialize()
    await store.create_server(ServerCreate(name="alpha", url="https://alpha.example.com/mcp"))
    gateway = create_gateway(store, startup_catalog="skip")
    app = _make_lifespan_app(gateway)

    baseline = len(gateway.mcp.providers)
    mock_catalog = AsyncMock(return_value=([], set()))
    with patch("fast_gateway.builder.collect_catalog", new=mock_catalog):
        async with LifespanManager(app):
            mock_catalog.assert_not_awaited()
            assert gateway.startup_catalog_task is None
            assert len(gateway.mcp.providers) == baseline + 1


async def test_startup_catalog_background_serves_then_refreshes() -> None:
    from unittest.mock import AsyncMock, patch

    from asgi_lifespan import LifespanManager

    from fast_gateway.models import ServerCreate

    store = SqliteStore(":memory:")
    await store.initialize()
    await store.create_server(ServerCreate(name="alpha", url="https://alpha.example.com/mcp"))
    gateway = create_gateway(store, startup_catalog="background")
    app = _make_lifespan_app(gateway)

    baseline = len(gateway.mcp.providers)
    mock_catalog = AsyncMock(return_value=([], set()))
    with patch("fast_gateway.builder.collect_catalog", new=mock_catalog):
        async with LifespanManager(app):
            assert len(gateway.mcp.providers) == baseline + 1
            assert gateway.startup_catalog_task is not None
            degraded = await gateway.startup_catalog_task
            assert degraded == []
            mock_catalog.assert_awaited()


async def test_startup_catalog_refresh_blocks_until_introspected() -> None:
    from unittest.mock import AsyncMock, patch

    from asgi_lifespan import LifespanManager

    from fast_gateway.models import ServerCreate

    store = SqliteStore(":memory:")
    await store.initialize()
    await store.create_server(ServerCreate(name="alpha", url="https://alpha.example.com/mcp"))
    gateway = create_gateway(store, startup_catalog="refresh")
    app = _make_lifespan_app(gateway)

    mock_catalog = AsyncMock(return_value=([], set()))
    with patch("fast_gateway.builder.collect_catalog", new=mock_catalog):
        async with LifespanManager(app):
            mock_catalog.assert_awaited()
            assert gateway.startup_catalog_task is None


async def test_admin_refresh_single_server_endpoint() -> None:
    from unittest.mock import AsyncMock, patch

    import httpx
    from asgi_lifespan import LifespanManager

    from fast_gateway.models import ServerCreate

    store = SqliteStore(":memory:")
    await store.initialize()
    created = await store.create_server(
        ServerCreate(name="alpha", url="https://alpha.example.com/mcp")
    )
    gateway = create_gateway(store, startup_catalog="skip")
    app = _make_lifespan_app(gateway)

    mock_catalog = AsyncMock(return_value=([], set()))
    with patch("fast_gateway.builder.collect_catalog", new=mock_catalog):
        async with LifespanManager(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
                ok = await client.post(f"/admin/servers/{created.id}/refresh")
                assert ok.status_code == 200
                assert ok.json() == {
                    "status": "refreshed",
                    "server_id": created.id,
                    "degraded": False,
                }
                missing = await client.post("/admin/servers/nope/refresh")
                assert missing.status_code == 404
    mock_catalog.assert_awaited()
