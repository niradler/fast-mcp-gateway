"""Smoke tests for the scaffold: the package imports, a gateway assembles, and the
admin routes are wired (returning 501 until the handlers are implemented)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import mcp_gateway
from mcp_gateway import Gateway, Hooks, SqliteStore, create_gateway


def make_app() -> tuple[FastAPI, Gateway]:
    gateway = create_gateway(SqliteStore(":memory:"), Hooks())
    app = FastAPI(lifespan=gateway.lifespan)
    gateway.install(app)
    return app, gateway


def test_version_is_exposed() -> None:
    assert isinstance(mcp_gateway.__version__, str)
    assert mcp_gateway.__version__


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
