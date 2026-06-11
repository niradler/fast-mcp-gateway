"""Tests for the Gateway's in-process programmatic MCP access.

``Gateway.client`` / ``list_tools`` / ``call_tool`` drive the parent FastMCP
server directly (no HTTP), so calls must still pass through the full governance
chain: hook middleware, access policy, and group scoping. Disabled servers feed
the policy without requiring live upstreams; the catalog is seeded directly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastmcp.exceptions import ToolError

from fast_gateway.app import Gateway, create_gateway
from fast_gateway.models import CatalogTool, GroupCreate, ServerCreate
from fast_gateway.store.sqlite import SqliteStore

_OPEN_STORES: list[SqliteStore] = []


@pytest.fixture(autouse=True)
async def _close_open_stores() -> AsyncIterator[None]:
    yield
    while _OPEN_STORES:
        await _OPEN_STORES.pop().close()


def _catalog() -> list[CatalogTool]:
    return [
        CatalogTool(server_id="s-math", namespace="math", name="math_add", bare_name="add"),
        CatalogTool(
            server_id="s-math", namespace="math", name="math_delete_all", bare_name="delete_all"
        ),
        CatalogTool(server_id="s-text", namespace="text", name="text_upper", bare_name="upper"),
    ]


async def _governed_gateway() -> Gateway:
    """A gateway whose policy knows two servers and one group, with no live upstreams.

    The servers are registered disabled so ``reload`` skips mounting and
    introspection but still rebuilds the access policy from them; the catalog
    snapshot is seeded afterwards so ``tools/list`` has content to filter.
    """
    store = SqliteStore(":memory:")
    await store.initialize()
    _OPEN_STORES.append(store)
    math = await store.create_server(
        ServerCreate(
            name="math", url="https://math.example.com/mcp", enabled=False, deny=["delete_*"]
        )
    )
    await store.create_server(
        ServerCreate(name="text", url="https://text.example.com/mcp", enabled=False)
    )
    await store.create_group(GroupCreate(name="analytics", member_server_ids=[math.id]))
    gateway = create_gateway(store, list_mode="all")
    await gateway.reload()
    await store.replace_catalog(_catalog())
    return gateway


async def test_list_tools_applies_server_policy() -> None:
    gateway = await _governed_gateway()
    names = {t.name for t in await gateway.list_tools()}
    assert {"math_add", "text_upper", "search_tools", "describe_tool"} <= names
    assert "math_delete_all" not in names


async def test_list_tools_group_scopes_catalog() -> None:
    gateway = await _governed_gateway()
    names = {t.name for t in await gateway.list_tools(group="analytics")}
    assert "math_add" in names
    assert "text_upper" not in names
    assert "math_delete_all" not in names
    assert {"search_tools", "describe_tool"} <= names


async def test_call_tool_invokes_in_process() -> None:
    gateway = await _governed_gateway()

    @gateway.mcp.tool
    def add(a: int, b: int) -> int:
        return a + b

    result = await gateway.call_tool("add", {"a": 2, "b": 3})
    assert result.data == 5


async def test_call_tool_denied_by_server_policy() -> None:
    gateway = await _governed_gateway()
    with pytest.raises(ToolError, match="not permitted"):
        await gateway.call_tool("math_delete_all", {})


async def test_call_tool_outside_group_is_rejected() -> None:
    gateway = await _governed_gateway()
    with pytest.raises(ToolError, match="not permitted"):
        await gateway.call_tool("text_upper", {"value": "hi"}, group="analytics")


async def test_meta_mode_is_the_default_listing() -> None:
    """Without list_mode='all' the catalog stays hidden; only meta-tools list."""
    store = SqliteStore(":memory:")
    await store.initialize()
    _OPEN_STORES.append(store)
    await store.create_server(
        ServerCreate(name="math", url="https://math.example.com/mcp", enabled=False)
    )
    gateway = create_gateway(store)
    await gateway.reload()
    await store.replace_catalog(_catalog())
    names = {t.name for t in await gateway.list_tools()}
    assert names == {"search_tools", "describe_tool", "invoke_tool"}


async def test_invoke_tool_enforces_server_policy() -> None:
    """invoke_tool routes through the same governance: a denied tool stays denied."""
    gateway = await _governed_gateway()
    with pytest.raises(ToolError, match="not permitted"):
        await gateway.call_tool("invoke_tool", {"name": "math_delete_all", "arguments": {}})


async def test_invoke_tool_enforces_group_scope() -> None:
    """invoke_tool honours the request's group, even through the nested dispatch."""
    gateway = await _governed_gateway()
    with pytest.raises(ToolError, match="not permitted"):
        await gateway.call_tool(
            "invoke_tool", {"name": "text_upper", "arguments": {}}, group="analytics"
        )


async def test_client_batches_calls_over_one_session() -> None:
    gateway = await _governed_gateway()

    @gateway.mcp.tool
    def double(value: int) -> int:
        return value * 2

    async with gateway.client() as client:
        first = await client.call_tool("double", {"value": 2})
        second = await client.call_tool("double", {"value": 21})
    assert first.data == 4
    assert second.data == 42
