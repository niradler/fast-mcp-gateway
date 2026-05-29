"""Tests for the admin server CRUD endpoints, driven through a TestClient so the
startup lifespan (store init + reload) runs like in production."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mcp_gateway import Hooks, SqliteStore, create_gateway


@pytest.fixture
def client() -> Iterator[TestClient]:
    gateway = create_gateway(SqliteStore(":memory:"), Hooks())
    app = FastAPI(lifespan=gateway.lifespan)
    gateway.install(app)
    with TestClient(app) as test_client:
        yield test_client


def make_payload(name: str = "weather") -> dict[str, object]:
    return {"name": name, "url": "https://example.com/mcp", "transport": "http"}


def test_create_and_get_server(client: TestClient) -> None:
    created = client.post("/admin/servers", json=make_payload())
    assert created.status_code == 201
    server_id = created.json()["id"]
    assert server_id

    fetched = client.get(f"/admin/servers/{server_id}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "weather"


def test_create_duplicate_name_conflicts(client: TestClient) -> None:
    assert client.post("/admin/servers", json=make_payload()).status_code == 201
    duplicate = client.post("/admin/servers", json=make_payload())
    assert duplicate.status_code == 409


def test_get_missing_returns_404(client: TestClient) -> None:
    assert client.get("/admin/servers/nope").status_code == 404


def test_update_server(client: TestClient) -> None:
    server_id = client.post("/admin/servers", json=make_payload()).json()["id"]
    patched = client.patch(f"/admin/servers/{server_id}", json={"enabled": False})
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False


def test_update_missing_returns_404(client: TestClient) -> None:
    assert client.patch("/admin/servers/nope", json={"enabled": False}).status_code == 404


def test_delete_server(client: TestClient) -> None:
    server_id = client.post("/admin/servers", json=make_payload()).json()["id"]
    assert client.delete(f"/admin/servers/{server_id}").status_code == 204
    assert client.get(f"/admin/servers/{server_id}").status_code == 404


def test_delete_missing_returns_404(client: TestClient) -> None:
    assert client.delete("/admin/servers/nope").status_code == 404


def test_test_endpoint_reports_unreachable_upstream(client: TestClient) -> None:
    payload = make_payload()
    payload["url"] = "http://127.0.0.1:9/mcp"
    payload["timeout_seconds"] = 2.0
    server_id = client.post("/admin/servers", json=payload).json()["id"]

    result = client.post(f"/admin/servers/{server_id}/test")
    assert result.status_code == 200
    body = result.json()
    assert body["ok"] is False
    assert body["error"]


def test_tools_endpoint_502_on_unreachable_upstream(client: TestClient) -> None:
    payload = make_payload()
    payload["url"] = "http://127.0.0.1:9/mcp"
    payload["timeout_seconds"] = 2.0
    server_id = client.post("/admin/servers", json=payload).json()["id"]

    result = client.get(f"/admin/servers/{server_id}/tools")
    assert result.status_code == 502
