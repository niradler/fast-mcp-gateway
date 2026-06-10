"""Tests for ToolsApiPlugin: the REST bridge over the gateway's MCP tools.

Routes are exercised over ASGI with the gateway lifespan running, against a
governed registry (disabled servers feed the policy; the catalog is seeded
directly) plus one live local tool for invocation. The lifespan is entered
inside each test (not a fixture) so the MCP session manager's task group
starts and exits in the same task.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI

from fast_gateway.app import create_gateway
from fast_gateway.models import CatalogTool, GroupCreate, ServerCreate
from fast_gateway.plugins.tools_api import ToolsApiPlugin
from fast_gateway.store.sqlite import SqliteStore


def _catalog() -> list[CatalogTool]:
    return [
        CatalogTool(
            server_id="s-math",
            namespace="math",
            name="math_add",
            bare_name="add",
            title="Add",
            description="Add two numbers.",
            parameters={"type": "object", "properties": {"a": {"type": "integer"}}},
        ),
        CatalogTool(
            server_id="s-math", namespace="math", name="math_delete_all", bare_name="delete_all"
        ),
        CatalogTool(server_id="s-text", namespace="text", name="text_upper", bare_name="upper"),
    ]


@contextlib.asynccontextmanager
async def _api() -> AsyncIterator[httpx.AsyncClient]:
    store = SqliteStore(":memory:")
    await store.initialize()
    math = await store.create_server(
        ServerCreate(
            name="math", url="https://math.example.com/mcp", enabled=False, deny=["delete_*"]
        )
    )
    await store.create_server(
        ServerCreate(name="text", url="https://text.example.com/mcp", enabled=False)
    )
    await store.create_group(GroupCreate(name="analytics", member_server_ids=[math.id]))

    gateway = create_gateway(store, plugins=[ToolsApiPlugin()])

    @gateway.mcp.tool
    def add(a: int, b: int) -> int:
        return a + b

    app = FastAPI(lifespan=gateway.lifespan)
    gateway.install(app)
    async with gateway.lifespan(app):
        await store.replace_catalog(_catalog())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            yield client


async def test_list_tools_applies_policy() -> None:
    async with _api() as api:
        response = await api.get("/admin/tools")
    assert response.status_code == 200
    names = {t["name"] for t in response.json()}
    assert {"math_add", "text_upper", "add", "search_tools"} <= names
    assert "math_delete_all" not in names


async def test_list_tools_group_scoped() -> None:
    async with _api() as api:
        response = await api.get("/admin/tools", params={"group": "analytics"})
    assert response.status_code == 200
    names = {t["name"] for t in response.json()}
    assert "math_add" in names
    assert "text_upper" not in names


async def test_describe_tool_returns_schema() -> None:
    async with _api() as api:
        response = await api.get("/admin/tools/math_add")
    assert response.status_code == 200
    detail = response.json()
    assert detail["name"] == "math_add"
    assert detail["input_schema"]["properties"]["a"]["type"] == "integer"


async def test_describe_unknown_tool_404() -> None:
    async with _api() as api:
        assert (await api.get("/admin/tools/nope")).status_code == 404


async def test_describe_out_of_group_tool_404() -> None:
    async with _api() as api:
        response = await api.get("/admin/tools/text_upper", params={"group": "analytics"})
    assert response.status_code == 404


async def test_call_tool_invokes_through_gateway() -> None:
    async with _api() as api:
        response = await api.post("/admin/tools/add/call", json={"arguments": {"a": 2, "b": 3}})
    assert response.status_code == 200
    body = response.json()
    assert body["is_error"] is False
    assert body["structured_content"] == {"result": 5}


async def test_call_denied_tool_reports_in_band_error() -> None:
    async with _api() as api:
        response = await api.post("/admin/tools/math_delete_all/call", json={"arguments": {}})
    assert response.status_code == 200
    body = response.json()
    assert body["is_error"] is True
    assert "not permitted" in body["content"][0]["text"]


async def test_call_out_of_group_tool_is_rejected() -> None:
    async with _api() as api:
        response = await api.post(
            "/admin/tools/text_upper/call",
            json={"arguments": {"value": "hi"}, "group": "analytics"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["is_error"] is True
    assert "not permitted" in body["content"][0]["text"]
